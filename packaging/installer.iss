; Inno Setup recipe for WallpaperFinder-Setup.exe.
;
; Wraps the folder packaging/wallpaper-finder.spec builds (dist\Wallpaper
; Finder). Installs for the current user only, under AppData\Local\Programs,
; so there is no admin prompt; adds a Start menu entry, offers a desktop one,
; and opens the program when it finishes. Uninstalls from Windows' Apps list.
;
;   iscc /DAppVersion=1.0.0 packaging\installer.iss

#define AppName "Wallpaper Finder"
#define AppExe "Wallpaper Finder.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7B0E5D7C-3C1E-4F43-9C55-2A8E1B6F9A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=paleLeap
AppPublisherURL=https://paleleap.com/projects/wallpaper-finder.html
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=WallpaperFinder-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
