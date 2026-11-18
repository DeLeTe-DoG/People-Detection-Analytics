#!/usr/bin/env bash
# Автозапуск на Linux-контроллере: служба systemd (сервер) + автозапуск киоск-панели в графической сессии.
#   sudo ./install_service.sh [light|standard|high]      (профиль по умолчанию — light)
set -e
if [ "$(id -u)" != "0" ]; then echo "Запустите через sudo"; exit 1; fi
DIR="$(cd "$(dirname "$0")" && pwd)"
USR="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
PROFILE="${1:-light}"
HOME_DIR="$(getent passwd "$USR" | cut -d: -f6)"

cat > /etc/systemd/system/office-analytics.service <<UNIT
[Unit]
Description=Office Analytics
After=network-online.target
Wants=network-online.target

[Service]
User=$USR
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/run.py --no-browser --profile $PROFILE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now office-analytics.service

# панель на дисплее: запускается при входе пользователя в графическую сессию
mkdir -p "$HOME_DIR/.config/autostart"
cat > "$HOME_DIR/.config/autostart/office-analytics-panel.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Office Analytics Panel
Exec=$DIR/venv/bin/python $DIR/run.py --kiosk-only
X-GNOME-Autostart-enabled=true
DESK
chown -R "$USR": "$HOME_DIR/.config/autostart"

echo "Служба установлена и запущена (профиль: $PROFILE)."
echo "Панель появится на экране после входа пользователя $USR в графическую сессию"
echo "(включите автовход в настройках ОС для работы без оператора)."
echo "Статус службы: systemctl status office-analytics   Журнал: journalctl -u office-analytics -f"
