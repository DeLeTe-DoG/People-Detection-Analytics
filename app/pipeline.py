"""Конвейер: камера -> детектор -> трекер -> движок активности -> БД. Работает в фоне."""
import json
import logging
import threading
import time

import cv2
import numpy as np

from .activity import ActivityEngine, STATE_LABELS
from .camera import CameraSource
from .detector import create_detector
from .faces import FaceEngine, Gallery
from .tracker import Tracker

log = logging.getLogger("pipeline")

WRIST_IDX = (9, 10)
ELBOW_IDX = (7, 8)


def _key_motion(tr):
    """Движение рук между кадрами (в долях роста) — по ключевым точкам."""
    k, pk = tr.kpts, tr.prev_kpts
    if k is None or pk is None:
        return 0.0
    ds = []
    for i in WRIST_IDX + ELBOW_IDX:
        if k[i][2] > 0.3 and pk[i][2] > 0.3:
            ds.append(float(np.hypot(k[i][0] - pk[i][0], k[i][1] - pk[i][1])))
    return (sum(ds) / len(ds)) / tr.height if ds else 0.0


class Pipeline:
    def __init__(self, cm, storage):
        self.cm = cm
        self.storage = storage
        self.camera = None
        self.detector = None
        self.detector_name = "загрузка..."
        self.detector_error = ""
        self.tracker = Tracker()
        self.engine = None
        self._applied = {}
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._status = {"people": [], "persons": [], "updated": 0}
        self._jpeg = (None, -1)
        self._jpeg_lock = threading.Lock()
        self.fps = 0.0
        self._prev_gray = None
        self._reload = threading.Event()
        self._last_purge = 0.0
        self.faces = None
        self.gallery = Gallery()
        self.faces_error = ""
        self._last_face = 0.0

    # ---------- управление ----------
    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pipeline", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        if self.camera:
            self.camera.stop()

    def apply_config(self):
        """Вызывается после изменения настроек — применяется в рабочем потоке."""
        self._reload.set()

    def _apply(self):
        cfg = self.cm.get()
        dump = lambda k: json.dumps(cfg.get(k), sort_keys=True)  # noqa: E731
        if self.engine is None:
            self.engine = ActivityEngine(cfg["activity"], cfg["zones"], self.storage)
        else:
            self.engine.set_config(cfg["activity"])
            self.engine.set_zones(cfg["zones"])
        self.tracker.lost_timeout = cfg["activity"]["lost_timeout"]
        self._apply_faces(cfg)

        if self.camera is None or self._applied.get("source") != dump("source"):
            if self.camera:
                self.camera.stop()
            self.camera = CameraSource(cfg["source"])
            self.camera.start()
            self._prev_gray = None
        if self.detector is None or self._applied.get("detector") != dump("detector"):
            self.detector_name = "загрузка..."
            self.detector_error = ""
            try:
                self.detector = create_detector(cfg["detector"])
                self.detector_name = self.detector.name
            except Exception as e:  # noqa: BLE001
                log.exception("Не удалось создать детектор")
                self.detector = None
                self.detector_error = str(e)
                self.detector_name = "ошибка"
        self._applied = {"source": dump("source"), "detector": dump("detector")}

    def _apply_faces(self, cfg):
        fc = cfg["faces"]
        want = bool(fc.get("enabled"))
        if want and (self.faces is None or self.faces.detect_width != int(fc["detect_width"])):
            self.faces = FaceEngine(fc["detect_width"])
            self.faces_error = "" if self.faces.available else self.faces.error
            if self.faces.available:
                log.info("Распознавание лиц включено")
            else:
                log.warning(self.faces.error)
        if not want:
            self.faces, self.faces_error = None, ""
        active = bool(self.faces and self.faces.available)
        self.engine.set_faces(active, fc["identify_wait"], fc["record_unknown"])
        self.reload_gallery()

    def reload_gallery(self):
        """Перечитать лица сотрудников из БД (после регистрации/удаления)."""
        fe = self.faces
        self.gallery.set(self.storage.load_gallery(fe.model_tag) if fe and fe.available else {})

    def faces_status(self):
        fe = self.faces
        return {"enabled": bool(fe), "available": bool(fe and fe.available),
                "error": self.faces_error, "enrolled": self.gallery.size()}

    # ---------- данные для интерфейса ----------
    def status(self):
        with self._lock:
            st = dict(self._status)
        cam = self.camera
        st["camera"] = {
            "status": cam.status if cam else "stopped",
            "error": cam.error if cam else "",
            "size": list(cam.size) if cam else [0, 0],
        }
        st["detector"] = self.detector_name
        st["detector_error"] = self.detector_error
        st["faces"] = self.faces_status()
        st["fps"] = round(self.fps, 1)
        return st

    def get_jpeg(self, max_w=960, quality=70):
        """Последний кадр камеры в JPEG (кэшируется по номеру кадра)."""
        cam = self.camera
        if cam is None:
            return None
        frame, fid, _ = cam.read()
        if frame is None:
            return None
        with self._jpeg_lock:
            if self._jpeg[1] == fid and self._jpeg[0] is not None:
                return self._jpeg[0]
        h, w = frame.shape[:2]
        if w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return None
        data = buf.tobytes()
        with self._jpeg_lock:
            self._jpeg = (data, fid)
        return data

    # ---------- рабочий цикл ----------
    def _loop(self):
        self._apply()
        last_fid = -1
        last_proc = 0.0
        fps_t = time.time()
        fps_n = 0
        while not self._stop.is_set():
            if self._reload.is_set():
                self._reload.clear()
                try:
                    self._apply()
                except Exception:  # noqa: BLE001
                    log.exception("Ошибка применения настроек")
            cfg = self.cm.get()
            interval = 1.0 / max(1.0, float(cfg["detector"].get("process_fps", 5)))
            now = time.time()
            if now - last_proc < interval or self.camera is None:
                time.sleep(0.01)
                continue
            frame, fid, ts = self.camera.read()
            if frame is None or fid == last_fid or self.detector is None:
                self._publish(now, [], (0, 0))
                time.sleep(0.05)
                continue
            last_fid, last_proc = fid, now
            try:
                self._process(frame, now)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка обработки кадра")
                time.sleep(0.2)
            fps_n += 1
            if now - fps_t >= 2.0:
                self.fps = fps_n / (now - fps_t)
                fps_t, fps_n = now, 0
            if now - self._last_purge > 3600:
                self._last_purge = now
                self.storage.purge_old(cfg["storage"]["retention_days"])
        log.info("Конвейер остановлен")

    def _process(self, frame, now):
        h, w = frame.shape[:2]
        dets = self.detector.detect(frame)
        tracks, expired = self.tracker.update(dets, now)

        small = cv2.resize(frame, (320, int(320 * h / w)))
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        mask = None
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            mask = cv2.absdiff(self._prev_gray, gray) > 25
        self._prev_gray = gray
        sx, sy = gray.shape[1] / w, gray.shape[0] / h
        for tr in tracks:
            if not tr.visible:
                continue
            ratio = 0.0
            if mask is not None:
                x1, y1, x2, y2 = tr.bbox
                crop = mask[max(0, int(y1 * sy)):int(y2 * sy) + 1, max(0, int(x1 * sx)):int(x2 * sx) + 1]
                if crop.size:
                    ratio = float(crop.mean())
            m = max(ratio, 0.5 * _key_motion(tr))
            tr.motion_ema = 0.5 * tr.motion_ema + 0.5 * m

        fcfg = self.cm.get()["faces"]
        if (self.faces and self.faces.available and self.gallery.size()
                and now - self._last_face >= 1.0 / max(0.2, float(fcfg["fps"]))):
            self._last_face = now
            try:
                self._identify(frame, tracks, now, fcfg)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка распознавания лиц")

        self.engine.update(tracks, expired, now, (w, h))
        self._publish(now, tracks, (w, h))

    def _identify(self, frame, tracks, now, fcfg):
        """Найти лица, отнести каждое к человеку в кадре и опознать по галерее."""
        thr, votes_needed = float(fcfg["threshold"]), int(fcfg["min_votes"])
        for row in self.faces.detect(frame, int(fcfg["min_face_px"])):
            cx, cy = row[0] + row[2] / 2.0, row[1] + row[3] / 2.0
            owner = None
            for tr in tracks:
                if not tr.visible:
                    continue
                x1, y1, x2, y2 = tr.bbox
                if x1 <= cx <= x2 and y1 <= cy <= y1 + 0.5 * (y2 - y1):    # лицо в верхней части рамки
                    area = (x2 - x1) * (y2 - y1)
                    if owner is None or area < owner[0]:
                        owner = (area, tr)
            if owner is None:
                continue
            tr = owner[1]
            if tr.emp_id is not None and now - tr.last_face_check < 10.0:
                continue                         # уже опознан; перепроверяем раз в 10 с
            tr.last_face_check = now
            eid, score = self.gallery.identify(self.faces.embed(frame, row), thr)
            if eid is None:
                continue
            if tr.emp_id is None:
                tr.face_votes[eid] = tr.face_votes.get(eid, 0) + 1
                if tr.face_votes[eid] >= votes_needed:
                    tr.emp_id = eid
                    log.info("Опознан сотрудник id=%s (схожесть %.2f)", eid, score)
            elif eid == tr.emp_id:
                tr.face_mismatch = 0
            else:                                # возможна подмена личности (люди пересеклись)
                tr.face_mismatch += 1
                if tr.face_mismatch >= 3:
                    tr.emp_id, tr.face_mismatch = eid, 0
                    log.info("Личность трека изменена на id=%s", eid)

    def _publish(self, now, tracks, size):
        w, h = size
        people = []
        persons = self.engine.snapshot(now) if self.engine else []
        by_track = {p["track_id"]: p for p in persons if p["track_id"] is not None}
        for tr in tracks:
            p = by_track.get(tr.id)
            if p is None or not tr.visible or not w:
                continue
            x1, y1, x2, y2 = tr.bbox
            people.append({
                "track_id": tr.id, "person_id": p["person_id"], "name": p["name"],
                "state": p["state"], "label": STATE_LABELS.get(p["state"], p["state"]),
                "employee": p.get("employee", False),
                "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
            })
        with self._lock:
            self._status = {"people": people, "persons": persons, "updated": now}
