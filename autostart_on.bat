@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Автозапуск при входе в Windows (без окна браузера)
schtasks /Create /F /SC ONLOGON /RL LIMITED /TN "OfficeAnalytics" /TR "cmd /c \"\"%~dp0start.bat\" --no-browser\"" >nul
if errorlevel 1 (echo Не удалось создать задачу автозапуска. Запустите от имени администратора.) else (echo Автозапуск включён.)
pause
