#!/usr/bin/env bash
# Установка на Linux/macOS (в т.ч. ARM-контроллеры):
#   ./install.sh         полная установка (YOLO-pose, точнее, тяжелее)
#   ./install.sh lite    облегчённая (без YOLO/torch — для слабых устройств)
set -e
cd "$(dirname "$0")"
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
if [ "$1" = "lite" ]; then
  pip install -r requirements-lite.txt
else
  pip install -r requirements.txt
fi
python3 download_models.py || echo "Модели лиц не скачались — положите их в models/ вручную (см. README)"
if [ "$1" != "lite" ]; then
  python3 -c "from ultralytics import YOLO; YOLO('yolov8n-pose.pt')"
fi
echo "Готово. Запуск: ./start.sh   |   Экран ПЛК: ./start.sh --kiosk   |   Служба: sudo ./install_service.sh"
