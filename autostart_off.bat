@echo off
chcp 65001 >nul
schtasks /Delete /F /TN "OfficeAnalytics" >nul 2>nul
schtasks /Delete /F /TN "OfficeAnalyticsKiosk" >nul 2>nul
echo Автозапуск выключен.
pause
