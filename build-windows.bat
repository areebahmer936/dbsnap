@echo off
REM Build script for dbsnap Windows installer
REM Run this on Windows with Python installed

echo === Building dbsnap Windows Installer ===

REM Install build dependencies
echo [1/4] Installing build dependencies...
pip install pyinstaller zstandard click jinja2 tqdm pyodbc

REM Build with PyInstaller
echo [2/4] Building executable with PyInstaller...
pyinstaller --clean dbsnap.spec

REM Verify build
echo [3/4] Verifying build...
if not exist "dist\dbsnap\dbsnap.exe" (
    echo ERROR: Build failed - dbsnap.exe not found
    exit /b 1
)
echo Build successful: dist\dbsnap\dbsnap.exe

REM Instructions for Inno Setup
echo.
echo [4/4] Inno Setup installer
echo.
echo To create the .exe installer:
echo   1. Download and install Inno Setup 6 from https://jrsoftware.org/isdl.php
echo   2. Open dbsnap-setup.iss in Inno Setup Compiler
echo   3. Click Build ^> Compile
echo   4. Installer will be at: dist\installer\dbsnap-1.1.0-setup.exe
echo.
echo Alternatively, run from command line:
echo   ISCC.exe dbsnap-setup.iss
echo.
echo === Done ===
