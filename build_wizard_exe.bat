@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "SKIP_APK=0"
set "REQUIRE_APK=0"
for %%A in (%*) do (
  if /i "%%~A"=="--skip-apk" set "SKIP_APK=1"
  if /i "%%~A"=="--require-apk" set "REQUIRE_APK=1"
)

if not exist "dist" mkdir "dist"

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
  echo Installer build failed!
  exit /b 1
)

if "%SKIP_APK%"=="1" (
  echo.
  echo Skipping Android APK build ^(--skip-apk^).
  goto :done
)

if not defined JAVA_HOME (
  if exist "C:\Program Files\Android\Android Studio\jbr\bin\java.exe" (
    set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"
  )
)

if not defined JAVA_HOME (
  if "%REQUIRE_APK%"=="1" (
    echo.
    echo ERROR: JAVA_HOME not set and Android Studio JBR not found. Cannot build APK.
    exit /b 1
  )
  echo.
  echo WARNING: JAVA_HOME not set and Android Studio JBR not found. Skipping APK build.
  goto :done
)

if not exist "givenergy_android\gradlew.bat" (
  if "%REQUIRE_APK%"=="1" (
    echo.
    echo ERROR: givenergy_android\gradlew.bat not found.
    exit /b 1
  )
  echo.
  echo WARNING: givenergy_android\gradlew.bat not found. Skipping APK build.
  goto :done
)

echo.
echo Building Android Companion App ^(givenergy-local-control.apk^) ...
pushd "givenergy_android"
call gradlew.bat assembleDebug
set "GRADLE_ERR=%ERRORLEVEL%"
popd
if not "%GRADLE_ERR%"=="0" (
  echo.
  echo Android APK build failed!
  exit /b %GRADLE_ERR%
)

copy /y "givenergy_android\app\build\outputs\apk\debug\givenergy-local-control-debug.apk" "dist\givenergy-local-control.apk" >nul
if errorlevel 1 (
  echo.
  echo Failed to copy APK to dist\givenergy-local-control.apk!
  exit /b 1
)

:done
echo.
echo Build succeeded:
echo   - "%~dp0dist\GivEnergy-Local-Control-Installer.exe"
if exist "dist\givenergy-local-control.apk" (
  echo   - "%~dp0dist\givenergy-local-control.apk"
)
endlocal
