"""Лёгкий трекер: связывает рамки соседних кадров в треки (greedy IoU + расстояние)."""
from collections import deque


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    def __init__(self, tid, det, now):
        self.id = tid
        self.bbox = det.bbox
        self.kpts = det.kpts
        self.first_seen = now
        self.last_seen = now
        self.hits = 1
        self.history = deque(maxlen=64)      # (t, ax, ay) в пикселях
        self.motion_ema = 0.0
        self.last_motion_ts = now
        self.visible = True
        self.prev_kpts = None
        self.first_anchor = None
        self.emp_id = None            # опознанный сотрудник (по лицу)
        self.face_votes = {}          # employee_id -> число совпадений
        self.face_mismatch = 0
        self.last_face_check = 0.0

    @property
    def height(self):
        return max(1.0, self.bbox[3] - self.bbox[1])

    def anchor(self):
        """Точка «опоры» человека: центр по X, низ корпуса по Y (стопы часто скрыты столом)."""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y1 + 0.8 * (y2 - y1))

    def update(self, det, now):
        self.prev_kpts = self.kpts
        self.bbox = det.bbox
        self.kpts = det.kpts
        self.last_seen = now
        self.hits += 1
        self.visible = True
        ax, ay = self.anchor()
        self.history.append((now, ax, ay))
        if self.first_anchor is None:
            self.first_anchor = (ax, ay)

    def speed(self, window=1.5):
        """Скорость в «ростах человека» в секунду за последние window секунд."""
        if len(self.history) < 2:
            return 0.0
        t1, x1, y1 = self.history[-1]
        for t0, x0, y0 in self.history:
            if t1 - t0 <= window:
                break
        dt = t1 - t0
        if dt < 0.4:
            return 0.0
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        return dist / dt / self.height

    def velocity(self, window=1.5):
        if len(self.history) < 2:
            return (0.0, 0.0)
        t1, x1, y1 = self.history[-1]
        for t0, x0, y0 in self.history:
            if t1 - t0 <= window:
                break
        dt = max(t1 - t0, 1e-3)
        return ((x1 - x0) / dt, (y1 - y0) / dt)


class Tracker:
    def __init__(self, lost_timeout=8.0, min_iou=0.1):
        self.lost_timeout = lost_timeout
        self.min_iou = min_iou
        self.tracks = {}
        self._next_id = 1

    def update(self, dets, now):
        """Вернуть (активные_треки, истёкшие_треки)."""
        pairs = []
        for tid, tr in self.tracks.items():
            tcx, tcy = (tr.bbox[0] + tr.bbox[2]) / 2, (tr.bbox[1] + tr.bbox[3]) / 2
            for di, d in enumerate(dets):
                o = iou(tr.bbox, d.bbox)
                dcx, dcy = (d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2
                dist = ((tcx - dcx) ** 2 + (tcy - dcy) ** 2) ** 0.5 / tr.height
                score = o + (0.25 if dist < 0.4 else 0.0) - 0.1 * dist
                if o >= self.min_iou or dist < 0.4:
                    pairs.append((score, tid, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for score, tid, di in pairs:
            if tid in used_t or di in used_d:
                continue
            self.tracks[tid].update(dets[di], now)
            used_t.add(tid)
            used_d.add(di)
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            tr = Track(self._next_id, d, now)
            ax, ay = tr.anchor()
            tr.history.append((now, ax, ay))
            tr.first_anchor = (ax, ay)
            self.tracks[self._next_id] = tr
            self._next_id += 1
        expired = []
        for tid in list(self.tracks):
            tr = self.tracks[tid]
            if tid not in used_t and tr.last_seen != now:
                tr.visible = False
                if now - tr.last_seen > self.lost_timeout:
                    expired.append(self.tracks.pop(tid))
        return list(self.tracks.values()), expired
