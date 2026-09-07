# Сборка ADK.exe (PowerShell). Аналог build_exe.bat.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q
python -c "import adk, PyQt6, ldap3, win32crypt; print('ok', adk.__version__)"
pyinstaller --noconfirm --clean adk.spec
Write-Host "`nГотово: dist\ADK\ADK.exe" -ForegroundColor Green
