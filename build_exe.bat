@echo off
rem ==============================================================================
rem                  ADK (Active Directory Kit) - Build Script
rem ==============================================================================
setlocal
cd /d "%~dp0"

echo ==============================================================================
echo                      ADK - Active Directory Kit Build
echo ==============================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [!] ERROR: Python 3.10+ is not found in PATH.
        echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
        goto :err
    )
    set "PY_CMD=py"
) else (
    set "PY_CMD=python"
)

if not exist .venv (
    echo [1/4] Creating virtual environment (.venv)...
    %PY_CMD% -m venv .venv || goto :err
)

call .venv\Scripts\activate.bat || goto :err

echo [2/4] Installing dependencies...
python -m pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q || goto :err

echo [3/4] Verifying imports...
python -c "import adk, PyQt6, ldap3, win32crypt; print('OK: ADK version', adk.__version__)" || goto :err

echo [4/4] Building standalone package with PyInstaller...
pyinstaller --noconfirm --clean adk.spec || goto :err

echo.
echo ==============================================================================
echo  BUILD SUCCESSFUL!
echo  Executable: "dist\ADK\ADK.exe"
echo  Config file: "%%USERPROFILE%%\Documents\ADK\config.ini"
echo ==============================================================================
echo.
pause
exit /b 0

:err
echo.
echo ==============================================================================
echo  [!] BUILD FAILED. Please check the error messages above.
echo ==============================================================================
echo.
pause
exit /b 1
