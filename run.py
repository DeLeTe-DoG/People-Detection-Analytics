"""Точка входа.

  python run.py                    сервер + обычный браузер
  python run.py --kiosk            сервер + полноэкранная панель на дисплее (ПЛК / панельный ПК)
  python run.py --kiosk-only       только панель на дисплее (сервер уже запущен как служба)
  python run.py --no-browser       только сервер (служба)
  python run.py --profile light    применить профиль производительности: light | standard | high
"""
import argparse
import logging
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import DATA_DIR, PROFILES, ConfigManager  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kiosk", action="store_true")
    ap.add_argument("--kiosk-only", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--profile", choices=sorted(PROFILES))
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(DATA_DIR, "app.log"), encoding="utf-8")],
    )
    cm = ConfigManager()
    if args.profile:
        cm.apply_profile(args.profile)
    srv = cm.get()["server"]
    host, port = srv["host"], int(srv["port"])
    base_url = f"http://localhost:{port}"

    from app.kiosk import run_kiosk
    stop = threading.Event()

    if args.kiosk_only:
        try:
            run_kiosk(base_url + "/panel", DATA_DIR, stop)
        except KeyboardInterrupt:
            stop.set()
        return

    from app.server import create_app
    app = create_app()
    print(f"\n  Office Analytics запущен: {base_url}\n  Экран для ПЛК: {base_url}/panel\n  Остановить: Ctrl+C\n")
    if args.kiosk:
        threading.Thread(target=run_kiosk, args=(base_url + "/panel", DATA_DIR, stop), daemon=True).start()
    elif not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(base_url)).start()
    try:
        try:
            from waitress import serve
            serve(app, host=host, port=port, threads=12)
        except ImportError:
            app.run(host=host, port=port, threaded=True)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
