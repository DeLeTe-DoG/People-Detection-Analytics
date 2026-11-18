"""Киоск-режим: полноэкранная панель на дисплее ПЛК/панельного ПК.

Запускает Chromium/Chrome/Edge с флагом --kiosk и следит за ним: если браузер закрыли
или он упал — перезапускает через несколько секунд.
"""
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser

log = logging.getLogger("kiosk")


def find_browser():
    cands = []
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LocalAppData")):
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
            path = shutil.which(name)
            if path:
                cands.append(path)
    return next((c for c in cands if os.path.exists(c)), None)


def wait_for_server(url, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url + "/api/meta", timeout=3).read()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    return False


def _command(browser, url, profile_dir):
    cmd = [browser, "--kiosk", "--noerrdialogs", "--disable-infobars", "--no-first-run",
           "--disable-session-crashed-bubble", "--overscroll-history-navigation=0",
           "--disable-pinch", "--password-store=basic", "--disable-features=TranslateUI",
           f"--user-data-dir={profile_dir}"]
    if platform.system() != "Windows" and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.append("--no-sandbox")        # Chromium не запускается от root без этого флага
    return cmd + [url]


def run_kiosk(url, data_dir, stop_event=None):
    """Блокирующий цикл: держит киоск-браузер запущенным. Вызывать в отдельном потоке."""
    stop_event = stop_event or threading.Event()
    if not wait_for_server(url):
        log.error("Сервер не отвечает: %s", url)
        return
    browser = find_browser()
    if not browser:
        log.warning("Chromium/Chrome/Edge не найден — открываю браузер по умолчанию (не полноэкранный)")
        webbrowser.open(url)
        return
    profile = os.path.join(data_dir, "kiosk-profile")
    os.makedirs(profile, exist_ok=True)
    while not stop_event.is_set():
        log.info("Запускаю киоск: %s", browser)
        try:
            proc = subprocess.Popen(_command(browser, url, profile))
            while proc.poll() is None and not stop_event.is_set():
                time.sleep(1)
            if stop_event.is_set():
                proc.terminate()
                break
            log.warning("Браузер закрылся, перезапуск через 5 с")
        except Exception:  # noqa: BLE001
            log.exception("Не удалось запустить браузер")
        stop_event.wait(5)
