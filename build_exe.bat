@echo off
rem Сборка ADK.exe (Windows). Требуется Python 3.10+ в PATH.
rem Результат: dist\ADK\ADK.exe  (+ папка с Qt-библиотеками — копировать целиком)
setlocal
cd /d "%~dp0"
if not exist .venv (
    echo [1/4] Создаю виртуальное окружение...
    python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
echo [2/4] Устанавливаю зависимости...
python -m pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q || goto :err
echo [3/4] Проверяю, что приложение импортируется...
python -c "import adk, PyQt6, ldap3, win32crypt; print('ok', adk.__version__)" || goto :err
echo [4/4] PyInstaller...
pyinstaller --noconfirm --clean adk.spec || goto :err
echo.
echo Готово: "dist\ADK\ADK.exe"
echo Первый запуск создаст %%USERPROFILE%%\Documents\ADK\config.ini — заполните LDAP-параметры.
exit /b 0
:err
echo.
echo СБОРКА НЕ УДАЛАСЬ (см. сообщения выше).
exit /b 1
