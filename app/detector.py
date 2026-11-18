"""Детекторы людей.

* YoloPoseDetector — YOLOv8-pose (ultralytics): рамка + 17 ключевых точек тела.
  Точный, основной режим. Веса скачиваются автоматически при первом запуске.
* HogDetector — встроенный в OpenCV детектор пешеходов. Не требует скачиваний
  и тяжёлых библиотек, но менее точен: запасной режим для проверки.
"""
import logging

import cv2
import numpy as np

log = logging.getLogger("detector")


class Detection:
    __slots__ = ("bbox", "conf", "kpts")

    def __init__(self, bbox, conf, kpts=None):
        self.bbox = tuple(float(v) for v in bbox)   # x1,y1,x2,y2 в пикселях
        self.conf = float(conf)
        self.kpts = kpts                            # ndarray (17,3) x,y,conf или None


class BaseDetector:
    name = "base"

    def detect(self, frame):
        raise NotImplementedError


class YoloPoseDetector(BaseDetector):
    name = "yolo-pose"

    def __init__(self, model="yolov8n-pose.pt", conf=0.4, imgsz=640, device="cpu"):
        from ultralytics import YOLO  # импорт здесь, чтобы приложение жило и без пакета
        self.model = YOLO(model)
        self.conf = conf
        self.imgsz = imgsz
        self.device = device if device in ("cpu", "mps") else (int(device) if str(device).isdigit() else device)
        # прогрев
        self.model.predict(np.zeros((imgsz, imgsz, 3), np.uint8), verbose=False, device=self.device)

    def detect(self, frame):
        res = self.model.predict(
            frame, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False
        )[0]
        out = []
        if res.boxes is None or len(res.boxes) == 0:
            return out
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        kps = res.keypoints.data.cpu().numpy() if res.keypoints is not None else None
        for i in range(len(boxes)):
            out.append(Detection(boxes[i], confs[i], kps[i] if kps is not None else None))
        return out


class HogDetector(BaseDetector):
    name = "opencv-hog"

    def __init__(self, conf=0.4, **_):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.min_weight = 0.5 + (conf - 0.4)

    def detect(self, frame):
        h, w = frame.shape[:2]
        scale = 640.0 / w if w > 640 else 1.0
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
        rects, weights = self.hog.detectMultiScale(
            small, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
        if len(rects) == 0:
            return []
        boxes = [list(map(int, r)) for r in rects]
        scores = [float(x) for x in np.array(weights).reshape(-1)]
        keep = cv2.dnn.NMSBoxes(boxes, scores, self.min_weight, 0.4)
        out = []
        for i in np.array(keep).reshape(-1):
            x, y, bw, bh = boxes[i]
            out.append(Detection(
                (x / scale, y / scale, (x + bw) / scale, (y + bh) / scale),
                min(1.0, scores[i] / 2.0 + 0.3),
            ))
        return out


def create_detector(cfg):
    """Создать детектор по настройкам. В режиме auto сначала YOLO, при отсутствии — HOG."""
    backend = cfg.get("backend", "auto")
    if backend in ("auto", "yolo"):
        try:
            det = YoloPoseDetector(
                cfg.get("model", "yolov8n-pose.pt"),
                cfg.get("conf", 0.4), cfg.get("imgsz", 640), cfg.get("device", "cpu"),
            )
            log.info("Детектор: YOLO-pose (%s)", cfg.get("model"))
            return det
        except Exception as e:  # noqa: BLE001
            if backend == "yolo":
                raise
            log.warning("YOLO недоступен (%s). Включён запасной детектор OpenCV-HOG — "
                        "он менее точен. Запустите install.bat для установки YOLO.", e)
    return HogDetector(conf=cfg.get("conf", 0.4))
