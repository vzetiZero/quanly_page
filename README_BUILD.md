Building the EXE (Windows)

1. Prerequisites
- Python 3.8+ installed and on PATH

2. Install dependencies and build
Open PowerShell in the project folder and run:

```powershell
.\build_exe.bat
```

3. Result
- The standalone executable is `dist\FBPageManager_TMV.exe`.
- Send the whole `dist` folder to end users so the editable Excel/config files and app assets stay next to the EXE.
- The release config uses `package_defaults\facebook_config.json`, which does not include private Facebook tokens.

Notes
- On first run PyInstaller may fetch and bundle many dependencies; the produced EXE size will be large (tens of MB).
- The build uses `FBPageManager_TMV.spec` so the icon, version info, Excel templates, JSON config, and resources are packaged consistently.
