"""Распознавание лиц сотрудников: YuNet (поиск лица) + SFace (эмбеддинг 128 чисел).

Обе модели входят в OpenCV (cv2.FaceDetectorYN / cv2.FaceRecognizerSF), работают на
процессоре, в том числе на ARM, и не требуют отдельных библиотек. Файлы моделей
скачивает `python download_models.py` (его вызывает install).

Фотографии НЕ сохраняются: в базе лежат только эмбеддинги (числовые векторы), по
которым лицо нельзя восстановить в виде картинки, но они остаются биометрическими
персональными данными.
"""
import logging
import os
import threading

import cv2
import numpy as np

from .config import BASE_DIR

log = logging.getLogger("faces")

MODELS_DIR = os.path.join(BASE_DIR, "models")
DET_MODEL = "face_detection_yunet_2023mar.onnx"
REC_MODEL_INT8 = "face_recognition_sface_2021dec_int8.onnx"   # основная: 10 МБ, точность 0.9932
REC_MODEL = "face_recognition_sface_2021dec.onnx"             # полная (37 МБ, 0.9940) — если положили вручную


def normalize(v):
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class Gallery:
    """Эмбеддинги всех зарегистрированных сотрудников; поиск по косинусной близости."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def set(self, data):
        with self._lock:
            self._data = {int(k): np.vstack([normalize(x) for x in v]).astype(np.float32)
                          for k, v in data.items() if len(v)}

    def size(self):
        with self._lock:
            return len(self._data)

    def identify(self, vec, threshold=0.40, margin=0.05):
        """Вернуть (employee_id | None, лучший_балл). Неоднозначные совпадения отбрасываются."""
        vec = normalize(vec)
        with self._lock:
            scores = sorted(((float(np.max(arr @ vec)), eid) for eid, arr in self._data.items()),
                            reverse=True)
        if not scores:
            return None, 0.0
        best, eid = scores[0]
        if best < threshold:
            return None, best
        if len(scores) > 1 and scores[1][0] >= threshold and best - scores[1][0] < margin:
            return None, best           # два похожих сотрудника — не рискуем
        return eid, best


class FaceEngine:
    def __init__(self, detect_width=1280):
        self.available = False
        self.error = ""
        self.detect_width = int(detect_width)
        self._lock = threading.Lock()
        det_path = os.path.join(MODELS_DIR, DET_MODEL)
        self.model_tag = REC_MODEL_INT8
        rec_path = os.path.join(MODELS_DIR, REC_MODEL_INT8)
        if not os.path.exists(rec_path):
            self.model_tag = REC_MODEL
            rec_path = os.path.join(MODELS_DIR, REC_MODEL)
        if not (os.path.exists(det_path) and os.path.exists(rec_path)):
            self.error = ("Не найдены модели распознавания лиц в папке models/. "
                          "Запустите: python download_models.py")
            return
        try:
            self._det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), 0.7, 0.3, 100)
            self._rec = cv2.FaceRecognizerSF.create(rec_path, "")
            self.available = True
        except Exception as e:  # noqa: BLE001
            self.error = f"Не удалось загрузить модели лиц: {e}"

    def detect(self, frame, min_px=40):
        """Найти лица. Возвращает список строк (15 чисел) в координатах исходного кадра."""
        if not self.available:
            return []
        h, w = frame.shape[:2]
        scale = min(1.0, self.detect_width / float(w))
        img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        with self._lock:
            self._det.setInputSize((img.shape[1], img.shape[0]))
            _, faces = self._det.detect(img)
        if faces is None:
            return []
        rows = faces.astype(np.float32).copy()
        rows[:, :14] /= scale
        rows = [r for r in rows if r[2] >= min_px]
        rows.sort(key=lambda r: -r[2] * r[3])
        return rows

    def embed(self, frame, row):
        with self._lock:
            aligned = self._rec.alignCrop(frame, row)
            feat = self._rec.feature(aligned)
        return normalize(feat)
