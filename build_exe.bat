@echo off
setlocal
REM Build script for Windows using PyInstaller.
cd /d "%~dp0"

REM 1) Install/update dependencies.
python -m pip install -r requirements.txt --upgrade
if errorlevel 1 exit /b 1

REM 2) Generate icon files.
python generate_icon.py
if errorlevel 1 exit /b 1

REM 3) Build the executable.
python -m PyInstaller --noconfirm --clean FBPageManager_TMV.spec
if errorlevel 1 exit /b 1

REM 4) Copy editable/support files next to the exe for end users.
if not exist dist mkdir dist
if exist package_defaults\facebook_config.json copy /Y package_defaults\facebook_config.json dist\facebook_config.json >nul
if exist selected_pages_config.xlsx copy /Y selected_pages_config.xlsx dist\selected_pages_config.xlsx >nul
if exist excel_config_template.xlsx copy /Y excel_config_template.xlsx dist\excel_config_template.xlsx >nul
if exist resources xcopy /E /I /Y resources dist\resources >nul

echo Build complete. The exe is dist\FBPageManager_TMV.exe
pause
