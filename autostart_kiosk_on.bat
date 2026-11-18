@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Автозапуск при входе в Windows: сервер + полноэкранная панель.
rem Для безлюдной работы включите автовход пользователя в Windows (netplwiz).
schtasks /Create /F /SC ONLOGON /RL LIMITED /TN "OfficeAnalyticsKiosk" /TR "cmd /c \"\"%~dp0start_kiosk.bat\"\"" >nul
if errorlevel 1 (echo Не удалось создать задачу автозапуска. Запустите от имени администратора.) else (echo Автозапуск экрана включён.)
pause
