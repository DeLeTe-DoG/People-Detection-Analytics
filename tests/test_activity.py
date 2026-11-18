"""Проверка логики определения активности на синтетических треках (без камеры).
Запуск:  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.activity import ActivityEngine  # noqa: E402
from app.detector import Detection  # noqa: E402
from app.faces import Gallery  # noqa: E402
import numpy as np  # noqa: E402
from app.storage import Storage  # noqa: E402
from app.tracker import Tracker  # noqa: E402

W, H = 1000, 800
CFG = {
    "min_state_seconds": 3.0, "idle_after_seconds": 45.0, "motion_threshold": 0.010,
    "walk_speed": 0.25, "lost_timeout": 8.0, "approach_margin": 0.07, "door_margin": 0.10,
    "max_absence_minutes": 120, "rematch_distance": 0.30,
}
ZONES = [
    {"id": "d1", "name": "Стол 1", "kind": "desk", "owner": "Иванов",
     "points": [[0.10, 0.45], [0.40, 0.45], [0.40, 0.90], [0.10, 0.90]]},
    {"id": "s1", "name": "Стеллаж A", "kind": "shelf",
     "points": [[0.60, 0.40], [0.80, 0.40], [0.80, 0.90], [0.60, 0.90]]},
    {"id": "wc", "name": "Туалет", "kind": "restroom",
     "points": [[0.88, 0.20], [0.99, 0.20], [0.99, 0.60], [0.88, 0.60]]},
]


def det(ax, ay, h=300.0, w=120.0):
    """Рамка человека так, чтобы точка опоры (80% высоты) оказалась в (ax, ay) пикселей."""
    y1 = ay - 0.8 * h
    return Detection((ax - w / 2, y1, ax + w / 2, y1 + h), 0.9)


class Sim:
    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        self.storage = Storage(os.path.join(self.tmp, "t.db"))
        self.engine = ActivityEngine(CFG, ZONES, self.storage)
        self.tracker = Tracker(lost_timeout=CFG["lost_timeout"])
        self.t = 1_000_000.0

    def step(self, dets, motion=0.0, dt=0.2):
        self.t += dt
        tracks, expired = self.tracker.update(dets, self.t)
        for tr in tracks:
            if tr.visible:
                tr.motion_ema = motion
        self.engine.update(tracks, expired, self.t, (W, H))

    def run(self, seconds, pos, motion=0.0):
        n = int(seconds / 0.2)
        for _ in range(n):
            self.step([det(*pos)] if pos else [], motion)

    def walk(self, a, b, seconds, motion=0.05):
        n = int(seconds / 0.2)
        for i in range(n):
            f = (i + 1) / n
            self.step([det(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)], motion)

    def states(self):
        return [p["state"] for p in self.engine.snapshot(self.t)]


class ActivityTests(unittest.TestCase):
    def test_working_then_idle(self):
        s = Sim()
        s.run(10, (250, 600), motion=0.03)
        self.assertEqual(s.states(), ["working"])
        s.run(60, (250, 600), motion=0.0)
        self.assertEqual(s.states(), ["sitting_idle"])

    def test_owner_name_from_desk(self):
        s = Sim()
        s.run(8, (250, 600), motion=0.03)
        self.assertEqual(s.engine.snapshot(s.t)[0]["name"], "Иванов")

    def test_walk_to_desk(self):
        s = Sim()
        s.walk((560, 790), (150, 520), 4.5)
        self.assertIn(s.states()[0], ("approach_desk", "walking"))
        s.run(8, (150, 520), motion=0.03)
        self.assertEqual(s.states(), ["working"])

    def test_shelf(self):
        s = Sim()
        s.walk((500, 700), (700, 650), 4)
        s.run(8, (700, 650), motion=0.02)
        self.assertEqual(s.states(), ["at_shelf"])

    def test_restroom_and_return(self):
        s = Sim()
        s.run(8, (250, 600), motion=0.03)
        pid = s.engine.snapshot(s.t)[0]["person_id"]
        s.walk((250, 600), (930, 420), 6)
        s.run(15, None)                       # пропал у двери туалета
        snap = s.engine.snapshot(s.t)
        self.assertEqual([p["state"] for p in snap], ["away_restroom"])
        s.run(300, None)                      # 5 минут отсутствует
        s.walk((930, 420), (250, 600), 6)     # вернулся через ту же дверь
        s.run(10, (250, 600), motion=0.03)
        snap = s.engine.snapshot(s.t)
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["person_id"], pid)   # тот же человек
        self.assertEqual(snap[0]["state"], "working")
        day = datetime.fromtimestamp(s.t).strftime("%Y-%m-%d")
        rep = s.storage.report(day)
        self.assertEqual(rep[0]["restroom_count"], 1)
        self.assertGreater(rep[0]["seconds"]["away_restroom"], 280)

    def test_two_people(self):
        s = Sim()
        for _ in range(40):
            s.step([det(250, 600), det(700, 650)], motion=0.03)
        self.assertEqual(sorted(s.states()), ["at_shelf", "working"])


class FaceIdentityTests(unittest.TestCase):
    def sim(self, record_unknown=True):
        s = Sim()
        s.engine.set_faces(True, identify_wait=12.0, record_unknown=record_unknown)
        s.eid = s.storage.add_employee("Петров")
        return s

    def identify(self, s):
        for tr in s.tracker.tracks.values():
            if tr.visible:
                tr.emp_id = s.eid

    def day(self, s):
        return datetime.fromtimestamp(s.t).strftime("%Y-%m-%d")

    def test_identified_before_wait_ends(self):
        s = self.sim()
        s.run(5, (250, 600), motion=0.03)
        self.assertEqual(s.states(), [])              # ещё ждём опознания
        self.identify(s)
        s.run(10, (250, 600), motion=0.03)
        snap = s.engine.snapshot(s.t)
        self.assertEqual([(p["name"], p["employee"], p["state"]) for p in snap], [("Петров", True, "working")])
        rep = [r for r in s.storage.report(self.day(s), include_unknown=False) if r["is_employee"]]
        self.assertGreater(rep[0]["seconds"]["working"], 10)

    def test_unknown_then_identified_merges_history(self):
        s = self.sim()
        s.run(20, (250, 600), motion=0.03)            # 12 с ожидания прошли -> «Неизвестный»
        snap = s.engine.snapshot(s.t)
        self.assertTrue(snap[0]["name"].startswith("Неизвестный"))
        self.identify(s)
        s.run(5, (250, 600), motion=0.03)
        snap = s.engine.snapshot(s.t)
        self.assertEqual([(p["name"], p["employee"]) for p in snap], [("Петров", True)])
        rep = s.storage.report(self.day(s))
        self.assertEqual([r["name"] for r in rep], ["Петров"])      # аноним растворился
        self.assertGreater(rep[0]["seconds"]["working"], 15)

    def test_unknown_not_recorded_when_disabled(self):
        s = self.sim(record_unknown=False)
        s.run(30, (250, 600), motion=0.03)
        self.assertEqual(s.states(), [])

    def test_employee_returns_with_same_identity(self):
        s = self.sim()
        s.run(3, (250, 600), motion=0.03)
        self.identify(s)
        s.run(10, (250, 600), motion=0.03)
        s.walk((250, 600), (930, 420), 6)
        s.run(15, None)
        self.assertEqual(s.states(), ["away_restroom"])
        s.run(200, None)
        s.walk((930, 420), (700, 650), 5)             # вернулся и пошёл к стеллажу
        self.identify(s)
        s.run(10, (700, 650), motion=0.02)
        snap = s.engine.snapshot(s.t)
        self.assertEqual([(p["name"], p["state"]) for p in snap], [("Петров", "at_shelf")])
        rep = s.storage.report(self.day(s), include_unknown=False)
        r = [x for x in rep if x["name"] == "Петров"][0]
        self.assertEqual(r["restroom_count"], 1)
        self.assertGreater(r["seconds"]["away_restroom"], 190)
        self.assertGreater(r["seconds"].get("at_shelf", 0), 5)

    def test_two_employees_not_mixed(self):
        s = self.sim()
        e2 = s.storage.add_employee("Сидоров")
        for _ in range(60):
            s.step([det(250, 600), det(700, 650)], motion=0.03)
            for tr in s.tracker.tracks.values():
                cx = (tr.bbox[0] + tr.bbox[2]) / 2
                tr.emp_id = s.eid if cx < 500 else e2
        got = {p["name"]: p["state"] for p in s.engine.snapshot(s.t)}
        self.assertEqual(got, {"Петров": "working", "Сидоров": "at_shelf"})

    def test_gallery_threshold_and_ambiguity(self):
        rng = np.random.default_rng(1)
        a, b = rng.normal(size=128), rng.normal(size=128)
        g = Gallery()
        g.set({1: [a], 2: [b]})
        self.assertEqual(g.identify(a + 0.1 * rng.normal(size=128))[0], 1)
        self.assertIsNone(g.identify(rng.normal(size=128))[0])       # чужой человек
        g.set({1: [a], 2: [a + 0.01 * rng.normal(size=128)]})        # двойники -> неоднозначно
        self.assertIsNone(g.identify(a)[0])


if __name__ == "__main__":
    unittest.main()
