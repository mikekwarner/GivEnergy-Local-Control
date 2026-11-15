@echo off
setlocal
cd /d "%~dp0"

echo Building standalone GivEnergy-Local-Control-Installer.exe ...
".venv\Scripts\pyinstaller.exe" ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name GivEnergy-Local-Control-Installer ^
  --icon "givenergy_web\static\icon.ico" ^
  --add-data "givenergy_web;givenergy_web" ^
  wizard.py

if errorlevel 1 (
  echo.
  echo Build failed!
  exit /b 1
)

echo.
echo Build succeeded: "%~dp0dist\GivEnergy-Local-Control-Installer.exe"
endlocal
