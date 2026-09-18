@echo off
rem Сборка ADK.exe (Windows). Требуется Python 3.10+ в PATH.
rem Результат: dist\ADK.exe + dist\_internal\ (копировать папку dist целиком)
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist .venv\Scripts\activate.bat (
    echo [1/4] Создаю виртуальное окружение...
    if exist .venv rmdir /s /q .venv 2>nul
    python -m venv .venv 2>nul
)

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

echo [2/4] Устанавливаю зависимости...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q || goto :err

echo [3/4] Проверяю, что приложение импортируется...
python -c "import adk, PyQt6, ldap3, win32crypt; print('ok', adk.__version__)" || goto :err

echo [4/4] PyInstaller...
python -m PyInstaller --noconfirm --clean adk.spec || goto :err

rem PyInstaller собирает в dist\ADK\ — поднимаем содержимое в dist\, чтобы ADK.exe и _internal лежали прямо там
if exist dist\_internal rmdir /s /q dist\_internal
if exist dist\ADK.exe del /q dist\ADK.exe
move /y dist\ADK\* dist\ >nul
move /y dist\ADK\_internal dist\_internal >nul
rmdir /s /q dist\ADK

echo.
echo ==============================================================================
echo Готово: "dist\ADK.exe" (рядом папка _internal — копировать dist целиком)
echo Первый запуск создаст %%USERPROFILE%%\Documents\ADK\config.ini — заполните LDAP-параметры.
echo ==============================================================================
echo.
pause
exit /b 0

:err
echo.
echo ==============================================================================
echo СБОРКА НЕ УДАЛАСЬ (см. сообщения выше).
echo ==============================================================================
echo.
pause
exit /b 1
