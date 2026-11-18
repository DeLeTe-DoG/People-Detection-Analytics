"""Конфигурация приложения: хранится в data/config.json, правится через веб-интерфейс."""
import copy
import json
import os
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("OFFICE_DATA_DIR", os.path.join(BASE_DIR, "data"))
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "analytics.db")

DEFAULT_CONFIG = {
    # Источник видео: usb (веб-камера/USB), url (RTSP/HTTP Wi-Fi камера), file (видеофайл для теста)
    "source": {
        "type": "usb",
        "usb_index": 0,
        "url": "",
        "file": "",
        "width": 1280,
        "height": 720,
    },
    "detector": {
        "backend": "auto",            # auto | yolo | hog
        "model": "yolov8n-pose.pt",   # yolov8n-pose.pt (быстро) / yolov8s-pose.pt (точнее)
        "conf": 0.4,
        "imgsz": 640,
        "device": "cpu",              # cpu | 0 (видеокарта NVIDIA)
        "process_fps": 5,
    },
    "activity": {
        "min_state_seconds": 3.0,     # состояние должно держаться столько секунд, чтобы зафиксироваться
        "idle_after_seconds": 45.0,   # сидит без движения дольше -> «Сидит без активности»
        "motion_threshold": 0.010,    # порог движения (доля изменившихся пикселей в рамке)
        "walk_speed": 0.25,           # скорость ходьбы в ростах человека в секунду
        "lost_timeout": 8.0,          # сколько секунд ждать пропавшего человека, прежде чем считать отсутствующим
        "approach_margin": 0.07,      # зона «подходит к столу/стеллажу» (доля кадра вокруг зоны)
        "door_margin": 0.10,          # насколько близко к двери нужно пропасть, чтобы считать «вышел»
        "max_absence_minutes": 120,   # сколько ждать возвращения до сброса личности
        "rematch_distance": 0.30,     # макс. расстояние для опознания вернувшегося человека
    },
    # Распознавание лиц сотрудников
    "faces": {
        "enabled": True,
        "threshold": 0.40,        # порог схожести (косинус); выше — строже, меньше ошибок
        "min_votes": 2,           # столько совпадений подряд нужно, чтобы признать человека
        "fps": 2.0,               # сколько раз в секунду искать лица
        "detect_width": 1280,     # ширина кадра для поиска лиц (больше — видит дальние лица)
        "min_face_px": 40,        # лица мельче этого размера игнорируются
        "identify_wait": 12.0,    # сколько секунд ждать опознания, прежде чем считать «Неизвестным»
        "record_unknown": True,   # вести учёт неопознанных людей
    },
    "ui": {"profile": "standard"},
    "workday": {"start": "08:00", "end": "18:00"},
    "storage": {"retention_days": 90},
    "server": {"host": "0.0.0.0", "port": 8080, "password": ""},   # пароль: логин admin
    # Зоны: {"id","name","kind","points":[[x,y],...] (0..1),"owner"}
    # kind: desk | shelf | rest | restroom | exit
    "zones": [],
}


# Профили производительности (для слабых ПК и промышленных контроллеров)
PROFILES = {
    "light": {"detector": {"process_fps": 2, "imgsz": 416, "model": "yolov8n-pose.pt"},
              "faces": {"fps": 1.0, "detect_width": 960}},
    "standard": {"detector": {"process_fps": 5, "imgsz": 640, "model": "yolov8n-pose.pt"},
                 "faces": {"fps": 2.0, "detect_width": 1280}},
    "high": {"detector": {"process_fps": 8, "imgsz": 960, "model": "yolov8s-pose.pt"},
             "faces": {"fps": 3.0, "detect_width": 1920}},
}


def _merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class ConfigManager:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._cfg = copy.deepcopy(DEFAULT_CONFIG)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.load()

    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        _merge(self._cfg, json.load(f))
                except (OSError, ValueError):
                    pass
            else:
                self.save()

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def get(self):
        with self._lock:
            return copy.deepcopy(self._cfg)

    def apply_profile(self, name):
        if name not in PROFILES:
            raise KeyError(name)
        patch = copy.deepcopy(PROFILES[name])
        patch["ui"] = {"profile": name}
        return self.update(patch)

    def update(self, patch):
        with self._lock:
            if "zones" in patch:
                self._cfg["zones"] = patch["zones"]
                patch = {k: v for k, v in patch.items() if k != "zones"}
            _merge(self._cfg, patch)
            self.save()
            return copy.deepcopy(self._cfg)
