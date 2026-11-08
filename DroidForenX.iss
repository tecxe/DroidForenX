; Inno Setup script for DroidForenX
; Builds the user-facing setup package: a standard installer that places the
; self-contained PyInstaller executable into Program Files and registers the
; uninstaller + shortcuts. The payload itself is a single-file, self-contained
; exe (Python runtime, Qt, bundled adb all inside), so the wizard only has to
; copy it - there are no external packages to install.

[Setup]
AppName=DroidForenX
AppVersion=1.0.41
AppVerName=DroidForenX v1.0.41
DefaultDirName={autopf}\DroidForenX
DefaultGroupName=DroidForenX
OutputBaseFilename=DroidForenX_Installer
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\DroidForenX.exe

[Files]
Source: "dist\DroidForenX.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DroidForenX"; Filename: "{app}\DroidForenX.exe"; WorkingDir: "{app}"
Name: "{commondesktop}\DroidForenX"; Filename: "{app}\DroidForenX.exe"; Tasks: desktopicon
Name: "{autoprograms}\DroidForenX"; Filename: "{app}\DroidForenX.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"
