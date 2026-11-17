<#
.SYNOPSIS
    Sets up a Windows development & build environment for GivEnergy Local Control
    after cloning from GitHub.
.DESCRIPTION
    1. Locates Python 3.11+ and creates/updates .venv with requirements.txt.
    2. Checks for Java (JDK / Android Studio JBR) for Android Gradle builds.
    3. Locates the Android SDK and generates givenergy_android\local.properties if missing.
    4. Checks for adb (Android Platform-Tools).
    5. Optionally runs build_wizard_exe.bat when -Build is passed.
#>
param(
    [switch]$Build,
    [switch]$SkipApk
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

Write-Host "=== GivEnergy Local Control — Windows Dev Environment Setup ===" -ForegroundColor Cyan

# 1. Locate Python 3
$PythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $ver = & py -3 --version 2>&1
        if ($LASTEXITCODE -eq 0) { $PythonCmd = @("py", "-3") }
    } catch {}
}
if (-not $PythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $ver = & python --version 2>&1
    if ($ver -match "Python 3\.") { $PythonCmd = @("python") }
}
if (-not $PythonCmd) {
    Write-Host "[ERROR] Python 3.11+ was not found on PATH." -ForegroundColor Red
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and re-run."
    exit 1
}

Write-Host "[1/4] Creating/verifying Python virtual environment (.venv)..." -ForegroundColor Yellow
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    if ($PythonCmd.Count -eq 2) {
        & $PythonCmd[0] $PythonCmd[1] -m venv .venv
    } else {
        & $PythonCmd[0] -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }
}

Write-Host "      Installing/updating dependencies from requirements.txt..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Write-Host "  [OK] Python .venv ready." -ForegroundColor Green

# 2. Check Java / JDK (for Android APK build)
Write-Host "[2/4] Checking Java JDK (required for Android APK build)..." -ForegroundColor Yellow
$JavaExe = $null
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
    $JavaExe = Join-Path $env:JAVA_HOME "bin\java.exe"
} elseif (Test-Path "C:\Program Files\Android\Android Studio\jbr\bin\java.exe") {
    $JavaExe = "C:\Program Files\Android\Android Studio\jbr\bin\java.exe"
    $env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
} elseif (Get-Command java -ErrorAction SilentlyContinue) {
    $JavaExe = (Get-Command java).Source
}

if ($JavaExe) {
    Write-Host "  [OK] Found Java: $JavaExe" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Java JDK not found. Android APK builds require Android Studio (JBR) or JDK 17+." -ForegroundColor DarkYellow
    Write-Host "         You can install Android Studio or run: winget install EclipseAdoptium.Temurin.17.JDK"
}

# 3. Locate Android SDK and write givenergy_android\local.properties
Write-Host "[3/4] Checking Android SDK & givenergy_android\local.properties..." -ForegroundColor Yellow
$SdkCandidates = @(
    $env:ANDROID_HOME,
    $env:ANDROID_SDK_ROOT,
    (Join-Path $env:LOCALAPPDATA "Android\Sdk")
) | Where-Object { $_ -and (Test-Path $_) }

$SdkDir = $SdkCandidates | Select-Object -First 1
$LocalPropsPath = Join-Path $RepoRoot "givenergy_android\local.properties"

if ($SdkDir) {
    if (-not (Test-Path $LocalPropsPath)) {
        $EscapedSdk = $SdkDir.Replace("\", "\\")
        Set-Content -Path $LocalPropsPath -Value "sdk.dir=$EscapedSdk" -Encoding ASCII
        Write-Host "  [OK] Created givenergy_android\local.properties -> $SdkDir" -ForegroundColor Green
    } else {
        Write-Host "  [OK] Android SDK found ($SdkDir) and local.properties already exists." -ForegroundColor Green
    }
} else {
    Write-Host "  [WARN] Android SDK not found at %LOCALAPPDATA%\Android\Sdk." -ForegroundColor DarkYellow
    Write-Host "         Install Android Studio + Android SDK if you plan to build givenergy-local-control.apk."
}

# 4. Check ADB
Write-Host "[4/4] Checking Android Debug Bridge (adb)..." -ForegroundColor Yellow
$AdbExe = $null
if (Get-Command adb -ErrorAction SilentlyContinue) {
    $AdbExe = (Get-Command adb).Source
} elseif ($SdkDir -and (Test-Path (Join-Path $SdkDir "platform-tools\adb.exe"))) {
    $AdbExe = Join-Path $SdkDir "platform-tools\adb.exe"
}
if ($AdbExe) {
    Write-Host "  [OK] Found adb: $AdbExe" -ForegroundColor Green
} else {
    Write-Host "  [INFO] adb not found on PATH (only needed for USB phone installs)." -ForegroundColor Gray
}

Write-Host ""
if ($Build) {
    Write-Host "Running build_wizard_exe.bat..." -ForegroundColor Cyan
    if ($SkipApk) {
        & cmd /c "build_wizard_exe.bat --skip-apk"
    } else {
        & cmd /c "build_wizard_exe.bat"
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "Setup complete! Next steps:" -ForegroundColor Green
    Write-Host "  - Run the Deployment Wizard from source : .\deploy_wizard.bat"
    Write-Host "  - Build Windows EXE + Android APK       : .\build_wizard_exe.bat"
    Write-Host "  - Build Windows EXE only (no Android)   : .\build_wizard_exe.bat --skip-apk"
}
