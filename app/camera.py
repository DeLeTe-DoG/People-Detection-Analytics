"""Источники видео: USB/встроенная веб-камера, Wi-Fi (RTSP/HTTP) камера, видеофайл.

Чтение идёт в отдельном потоке и всегда отдаёт самый свежий кадр — так нет
накопления задержки у RTSP. При обрыве связи поток сам переподключается.
"""
import logging
import os
import platform
import threading
import time

import cv2

log = logging.getLogger("camera")

# Для RTSP надёжнее TCP (меньше разрывов по Wi-Fi). Должно быть задано до открытия потока.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|stimeout;5000000",
)


def _open_capture(src):
    t = src.get("type", "usb")
    if t == "usb":
        idx = int(src.get("usb_index", 0))
        if platform.system() == "Windows":
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(idx)
        else:
            cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(src.get("width", 1280)))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(src.get("height", 720)))
        return cap
    if t == "url":
        url = (src.get("url") or "").strip()
        if not url:
            return None
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap
    if t == "file":
        path = (src.get("file") or "").strip()
        if not path:
            return None
        return cv2.VideoCapture(path)
    return None


class CameraSource:
    def __init__(self, src_cfg):
        self.cfg = dict(src_cfg)
        self._lock = threading.Lock()
        self._frame = None
        self._frame_ts = 0.0
        self._frame_id = 0
        self._stop = threading.Event()
        self._thread = None
        self.status = "stopped"      # stopped | connecting | online | error
        self.error = ""
        self.size = (0, 0)
        self.is_file = src_cfg.get("type") == "file"

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.status = "stopped"

    def read(self):
        """Вернуть (frame, frame_id, ts) — последний кадр или (None, 0, 0)."""
        with self._lock:
            if self._frame is None:
                return None, 0, 0.0
            return self._frame.copy(), self._frame_id, self._frame_ts

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            self.status = "connecting"
            cap = None
            try:
                cap = _open_capture(self.cfg)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
            if cap is None or not cap.isOpened():
                self.status = "error"
                if not self.error:
                    self.error = "Не удалось открыть источник видео"
                log.warning("Камера недоступна: %s (повтор через %.0f с)", self.error, backoff)
                if cap is not None:
                    cap.release()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 15.0)
                continue

            backoff = 1.0
            self.error = ""
            self.status = "online"
            log.info("Камера подключена: %s", self.cfg.get("type"))
            fail = 0
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            while not self._stop.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self.is_file:           # видеофайл закончился -> по кругу
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        fail += 1
                        if fail > 3:
                            break
                        continue
                    fail += 1
                    if fail > 30:
                        log.warning("Поток прерван, переподключаюсь")
                        break
                    time.sleep(0.05)
                    continue
                fail = 0
                with self._lock:
                    self._frame = frame
                    self._frame_ts = time.time()
                    self._frame_id += 1
                    self.size = (frame.shape[1], frame.shape[0])
                if self.is_file:               # для файла держим реальную скорость
                    dt = 1.0 / max(fps, 1.0) - (time.time() - t0)
                    if dt > 0:
                        time.sleep(dt)
            cap.release()
            self.status = "error"
            self.error = "Связь с камерой потеряна"
            self._stop.wait(1.0)


def probe_usb_cameras(max_index=6):
    """Найти подключённые USB/встроенные камеры (индексы 0..N)."""
    found = []
    for i in range(max_index):
        try:
            if platform.system() == "Windows":
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ok, frame = cap.read()
                if ok:
                    h, w = frame.shape[:2]
                    found.append({"index": i, "name": f"Камера {i} ({w}x{h})"})
            cap.release()
        except Exception:  # noqa: BLE001
            pass
    return found
