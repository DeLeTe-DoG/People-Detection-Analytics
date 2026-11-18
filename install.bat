@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo   Office Analytics - установка
echo   (install.bat lite - облегчённая установка без YOLO для слабых устройств)
echo ==============================================

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo.
  echo [ОШИБКА] Python не найден. Установите Python 3.10-3.12 с https://www.python.org/downloads/
  echo При установке отметьте галочку "Add Python to PATH", затем запустите install.bat снова.
  pause
  exit /b 1
)

if not exist venv (
  echo Создаю окружение...
  %PY% -m venv venv
  if errorlevel 1 ( echo [ОШИБКА] Не удалось создать окружение & pause & exit /b 1 )
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

set REQ=requirements.txt
if /i "%1"=="lite" set REQ=requirements-lite.txt
echo.
echo Устанавливаю библиотеки из %REQ% (нужен интернет, до 15 минут)...
pip install -r %REQ%
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Не удалось установить библиотеки. Проверьте интернет и повторите.
  pause
  exit /b 1
)

echo.
echo Загружаю модели лиц...
python download_models.py

if /i not "%1"=="lite" (
  echo Загружаю модель распознавания людей ^(один раз^)...
  python -c "from ultralytics import YOLO; YOLO('yolov8n-pose.pt')"
)

echo.
echo ==============================================
echo   Готово!
echo   Обычный запуск:             start.bat
echo   Экран ПЛК (полный экран):   start_kiosk.bat
echo   Автозапуск экрана ПЛК:      autostart_kiosk_on.bat
echo ==============================================
pause
