@echo off
rem Кодировка UTF-8 для корректного вывода кириллицы в консоли Windows
chcp 65001 >nul
rem Сборка ADK.exe (Windows). Требуется Python 3.10+ в PATH.
rem Результат: dist\ADK\ADK.exe  (+ папка с Qt-библиотеками — копировать целиком)
setlocal
cd /d "%~dp0"

echo ==============================================================================
echo                      Сборка ADK — Active Directory Kit
echo ==============================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [!] ОШИБКА: Python не найден в PATH.
        echo Установите Python 3.10+ с официального сайта python.org,
        echo обязательно отметив галочку "Add python.exe to PATH".
        goto :err
    )
    set "PY_CMD=py"
) else (
    set "PY_CMD=python"
)

if not exist .venv (
    echo [1/4] Создаю виртуальное окружение .venv...
    %PY_CMD% -m venv .venv || goto :err
)

call .venv\Scripts\activate.bat || goto :err

echo [2/4] Устанавливаю и обновляю зависимости...
python -m pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q || goto :err

echo [3/4] Проверяю импорт модулей приложения...
python -c "import adk, PyQt6, ldap3, win32crypt; print('OK: ADK', adk.__version__)" || goto :err

echo [4/4] Запуск PyInstaller (сборка dist\ADK\ADK.exe)...
pyinstaller --noconfirm --clean adk.spec || goto :err

echo.
echo ==============================================================================
echo  Сборка успешно завершена!
echo  Исполняемый файл: "dist\ADK\ADK.exe"
echo  Папка конфигурации: %%USERPROFILE%%\Documents\ADK\config.ini
echo ==============================================================================
echo.
pause
exit /b 0

:err
echo.
echo ==============================================================================
echo  [!] СБОРКА НЕ УДАЛАСЬ (см. текст ошибки выше).
echo ==============================================================================
echo.
pause
exit /b 1
