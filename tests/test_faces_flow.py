"""Сквозная проверка цепочки лиц с подставным движком лиц (реальные модели не нужны):
регистрация через API -> галерея -> опознание трека в конвейере -> привязка к сотруднику."""
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["OFFICE_DATA_DIR"] = tempfile.mkdtemp()

import cv2  # noqa: E402
from app.activity import ActivityEngine  # noqa: E402
from app.detector import Detection  # noqa: E402
from app.faces import normalize  # noqa: E402
from app.server import create_app  # noqa: E402

RNG = np.random.default_rng(7)
VEC_A, VEC_B = normalize(RNG.normal(size=128)), normalize(RNG.normal(size=128))


class FakeFaces:
    available = True
    detect_width = 1280
    model_tag = "fake"

    def __init__(self):
        self.rows = []          # что «видит» детектор лиц
        self.vec = VEC_A        # какое лицо «читается»

    def detect(self, frame, min_px=40):
        return [np.array(r, dtype=np.float32) for r in self.rows]

    def embed(self, frame, row):
        return self.vec + 0.02 * RNG.normal(size=128).astype(np.float32)


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.pipe = cls.app.pipeline
        cls.c = cls.app.test_client()
        cls.pipe.stop()                       # свой цикл не нужен — гоняем шаги вручную
        cls.fake = FakeFaces()
        cls.pipe.faces = cls.fake
        # «камера» отдаёт один кадр
        class Cam:
            status, error, size = "online", "", (640, 480)
            def read(self_inner):
                return np.zeros((480, 640, 3), np.uint8), int(time.time() * 1000), time.time()
        cls.pipe.camera = Cam()

    def test_consent_required(self):
        r = self.c.post("/api/employees", json={"name": "Без согласия", "consent": False})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/employees", json={"name": "Без имени", "consent": True, "position": ""})
        self.assertEqual(r.status_code, 200)

    def test_enroll_and_identify(self):
        pid = self.c.post("/api/employees", json={"name": "Петров П.", "consent": True}).get_json()["id"]
        # два лица в кадре -> регистрация отклоняется
        self.fake.rows = [[10, 10, 80, 80] + [0] * 10 + [0.95], [200, 10, 80, 80] + [0] * 10 + [0.95]]
        r = self.c.post(f"/api/employees/{pid}/enroll", json={"shots": 1}).get_json()
        self.assertFalse(r["ok"])
        # одно лицо -> сохраняется
        self.fake.rows = [[10, 10, 80, 80] + [0] * 10 + [0.95]]
        r = self.c.post(f"/api/employees/{pid}/enroll", json={"shots": 2}).get_json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.pipe.gallery.size(), 1)
        # то же лицо нельзя повесить на другого сотрудника
        pid2 = self.c.post("/api/employees", json={"name": "Сидоров С.", "consent": True}).get_json()["id"]
        r = self.c.post(f"/api/employees/{pid2}/enroll", json={"shots": 1}).get_json()
        self.assertFalse(r["ok"])
        self.assertIn("другого", r["error"])

        # конвейер: человек в кадре, лицо в верхней части рамки
        from app.tracker import Tracker
        eng = ActivityEngine(self.app.pipeline.cm.get()["activity"], [], self.pipe.storage)
        eng.set_faces(True, 12.0, True)
        self.pipe.engine = eng
        self.pipe.tracker = Tracker()
        now = time.time()
        det = Detection((100, 100, 220, 400), 0.9)
        fcfg = self.pipe.cm.get()["faces"]
        frame = np.zeros((480, 640, 3), np.uint8)
        for i in range(4):
            now += 0.5
            tracks, _ = self.pipe.tracker.update([det], now)
            self.fake.rows = [[130, 110, 60, 70] + [0] * 10 + [0.95]]
            self.pipe._identify(frame, tracks, now, fcfg)
            eng.update(tracks, [], now, (640, 480))
        snap = eng.snapshot(now)
        self.assertEqual([(p["name"], p["employee"]) for p in snap], [("Петров П.", True)])
        # чужое лицо не опознаётся
        self.fake.vec = VEC_B
        det2 = Detection((400, 100, 520, 400), 0.9)
        tr_ids = set()
        for i in range(4):
            now += 0.5
            tracks, _ = self.pipe.tracker.update([det, det2], now)
            self.fake.rows = [[430, 110, 60, 70] + [0] * 10 + [0.95]]
            self.pipe._identify(frame, tracks, now, fcfg)
        stranger = [t for t in self.pipe.tracker.tracks.values() if t.bbox[0] > 300][0]
        self.assertIsNone(stranger.emp_id)

    def test_revoke_removes_faces(self):
        pid = self.c.post("/api/employees", json={"name": "Отзыв О.", "consent": True}).get_json()["id"]
        self.fake.vec = normalize(RNG.normal(size=128))
        self.fake.rows = [[10, 10, 80, 80] + [0] * 10 + [0.95]]
        self.assertTrue(self.c.post(f"/api/employees/{pid}/enroll", json={"shots": 1}).get_json()["ok"])
        n = self.pipe.gallery.size()
        self.c.post(f"/api/employees/{pid}/revoke")
        self.assertEqual(self.pipe.gallery.size(), n - 1)
        emp = [e for e in self.c.get("/api/employees").get_json()["employees"] if e["id"] == pid][0]
        self.assertEqual(emp["faces"], 0)

    def test_photo_upload(self):
        pid = self.c.post("/api/employees", json={"name": "Фото Ф.", "consent": True}).get_json()["id"]
        self.fake.vec = normalize(RNG.normal(size=128))
        self.fake.rows = [[10, 10, 120, 120] + [0] * 10 + [0.95]]
        ok, buf = cv2.imencode(".jpg", np.zeros((200, 200, 3), np.uint8))
        import io
        r = self.c.post(f"/api/employees/{pid}/photos", data={"photos": (io.BytesIO(buf.tobytes()), "a.jpg")},
                        content_type="multipart/form-data").get_json()
        self.assertTrue(r["ok"], r)

    def test_pages_and_profile(self):
        self.assertEqual(self.c.get("/panel").status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.post("/api/profile", json={"name": "nope"}).status_code, 400)
        self.assertTrue(self.c.post("/api/profile", json={"name": "light"}).get_json()["ok"])
        cfg = self.c.get("/api/config").get_json()
        self.assertEqual(cfg["detector"]["process_fps"], 2)
        self.assertEqual(cfg["faces"]["fps"], 1.0)


if __name__ == "__main__":
    unittest.main()
