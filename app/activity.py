"""Логика определения активности: зоны, состояния, сглаживание, личности, отсутствие."""
import time

import cv2
import numpy as np

from .storage import ANON_PREFIX

STATE_LABELS = {
    "working": "Работает за столом",
    "sitting_idle": "Сидит без активности",
    "at_desk_standing": "Стоит у стола",
    "approach_desk": "Подходит к столу",
    "at_shelf": "У складского стеллажа",
    "approach_shelf": "Идёт к стеллажу",
    "rest": "В зоне отдыха",
    "walking": "Идёт",
    "standing": "Стоит",
    "sitting": "Сидит",
    "away_restroom": "Отошёл (туалет)",
    "away_exit": "Вышел из помещения",
    "away_unknown": "Нет в кадре",
}
ABSENT_STATES = ("away_restroom", "away_exit", "away_unknown")
ZONE_KINDS = {
    "desk": "Рабочее место",
    "shelf": "Складской стеллаж",
    "rest": "Зона отдыха",
    "restroom": "Туалет (дверь)",
    "exit": "Выход (дверь)",
}
ZONE_PRIORITY = ["desk", "shelf", "rest", "restroom", "exit"]


class Zone:
    def __init__(self, d):
        self.id = d.get("id") or d.get("name")
        self.name = d.get("name") or str(self.id)
        self.kind = d.get("kind", "desk")
        self.owner = (d.get("owner") or "").strip()
        pts = np.array(d.get("points") or [], dtype=np.float32).reshape(-1, 2)
        self.valid = len(pts) >= 3
        self.contour = pts.reshape(-1, 1, 2)
        self.centroid = tuple(pts.mean(axis=0)) if len(pts) else (0.5, 0.5)

    def signed_dist(self, pt):
        """>0 — внутри (расстояние до границы), <0 — снаружи. В долях кадра."""
        return float(cv2.pointPolygonTest(self.contour, (float(pt[0]), float(pt[1])), True))


def posture(tr):
    """'sitting' | 'standing' | None (неизвестно). Использует ключевые точки тела YOLO-pose."""
    k = tr.kpts
    if k is None:
        return None

    def ys(idx):
        return [float(k[i][1]) for i in idx if k[i][2] > 0.3]

    sh, hip, kn = ys((5, 6)), ys((11, 12)), ys((13, 14))
    if not sh or not hip:
        return None
    torso = abs(np.mean(hip) - np.mean(sh))
    if torso < 5:
        return None
    if kn:
        thigh = abs(np.mean(kn) - np.mean(hip))
        return "sitting" if thigh / torso < 0.6 else "standing"
    # колен не видно (закрыты столом): по пропорциям рамки
    return "sitting" if tr.height / torso < 2.1 else "standing"


class Person:
    def __init__(self, pid, name):
        self.id = pid
        self.name = name
        self.track_id = None
        self.state = None
        self.zone = None
        self.since = 0.0
        self.row_id = None
        self.cand = None            # (state, zone, since)
        self.force_commit = True
        self.absent_since = None
        self.last_pos = (0.5, 0.5)
        self.exit_zone = None
        self.last_touch = 0.0
        self.employee = False       # опознан по лицу (зарегистрированный сотрудник)


class ActivityEngine:
    def __init__(self, acfg, zones, storage):
        self.cfg = dict(acfg)
        self.storage = storage
        self.set_zones(zones)
        self.persons = {}           # id -> Person (активные)
        self.track_to_person = {}
        self.faces_on = False
        self.identify_wait = 12.0
        self.record_unknown = True

    def set_faces(self, enabled, identify_wait=12.0, record_unknown=True):
        self.faces_on = bool(enabled)
        self.identify_wait = float(identify_wait)
        self.record_unknown = bool(record_unknown)

    def set_config(self, acfg):
        self.cfg = dict(acfg)

    def set_zones(self, zones):
        self.zones = [z for z in (Zone(d) for d in zones) if z.valid]

    # ---------- вспомогательное ----------
    def _zone_at(self, pos):
        found = [z for z in self.zones if z.signed_dist(pos) >= 0]
        for kind in ZONE_PRIORITY:
            for z in found:
                if z.kind == kind:
                    return z
        return None

    def _near_zone(self, pos, kinds, margin):
        best, bd = None, -1e9
        for z in self.zones:
            if z.kind in kinds:
                d = z.signed_dist(pos)
                if d >= -margin and d > bd:
                    best, bd = z, d
        return best

    def _classify(self, tr, size):
        c = self.cfg
        w, h = size
        ax, ay = tr.anchor()
        pos = (ax / w, ay / h)
        zone = self._zone_at(pos)
        walking = tr.speed() > c["walk_speed"]
        post = posture(tr)
        now_idle = (tr.history[-1][0] - tr.last_motion_ts) if tr.history else 0.0

        if zone is not None and zone.kind == "desk":
            if walking:
                return "approach_desk", zone
            if post == "standing":
                return "at_desk_standing", zone
            return ("working" if now_idle < c["idle_after_seconds"] else "sitting_idle"), zone
        if zone is not None and zone.kind == "shelf":
            return ("approach_shelf" if walking else "at_shelf"), zone
        if zone is not None and zone.kind == "rest":
            return "rest", zone

        if walking:
            near = self._near_zone(pos, ("desk", "shelf"), c["approach_margin"])
            if near is not None:
                vx, vy = tr.velocity()
                vx, vy = vx / w, vy / h
                dx, dy = near.centroid[0] - pos[0], near.centroid[1] - pos[1]
                if vx * dx + vy * dy > 0:
                    return ("approach_desk" if near.kind == "desk" else "approach_shelf"), near
            return "walking", None
        return ("sitting" if post == "sitting" else "standing"), None

    def _absence_state(self, pos):
        z = self._near_zone(pos, ("restroom", "exit"), self.cfg["door_margin"])
        if z is None:
            return "away_unknown", None
        return ("away_restroom" if z.kind == "restroom" else "away_exit"), z

    # ---------- работа с интервалами ----------
    def _commit(self, p, state, zone, start):
        if p.row_id is not None:
            self.storage.touch_interval(p.row_id, start)
        zname = zone.name if zone is not None else None
        p.row_id = self.storage.open_interval(p.id, state, zname, start)
        p.state, p.zone, p.since = state, zname, start
        p.cand = None
        p.force_commit = False
        if (zone is not None and zone.kind == "desk" and zone.owner
                and not self.faces_on and not p.employee and p.name.startswith(ANON_PREFIX)
                and not any(o.name == zone.owner for o in self.persons.values())):
            p.name = zone.owner
            self.storage.rename_person(p.id, zone.owner)

    def _new_person(self):
        pid = self.storage.add_person("")
        p = Person(pid, self.storage.persons()[pid])
        self.persons[pid] = p
        return p

    def _assign(self, tr, now, size):
        w, h = size
        fa = tr.first_anchor or tr.anchor()
        pos = (fa[0] / w, fa[1] / h)
        max_abs = self.cfg["max_absence_minutes"] * 60
        cands = []
        for p in self.persons.values():
            if p.employee or p.track_id is not None or p.state not in ABSENT_STATES:
                continue
            if now - p.absent_since > max_abs:
                continue
            d = ((pos[0] - p.last_pos[0]) ** 2 + (pos[1] - p.last_pos[1]) ** 2) ** 0.5
            ok = d <= self.cfg["rematch_distance"]
            if not ok and p.exit_zone is not None:
                ok = p.exit_zone.signed_dist(pos) >= -2 * self.cfg["door_margin"]
            if ok:
                cands.append(p)
        if cands:
            p = min(cands, key=lambda x: x.absent_since)
            if p.row_id is not None:
                self.storage.touch_interval(p.row_id, tr.first_seen)
            p.row_id = None
            p.state = None
            p.absent_since = None
            p.exit_zone = None
        else:
            p = self._new_person()
        p.track_id = tr.id
        p.force_commit = True
        p.cand = None
        p.since = tr.first_seen
        self.track_to_person[tr.id] = p.id
        return p

    def _employee_person(self, eid):
        p = self.persons.get(eid)
        if p is None:
            name = self.storage.person_name(eid) or f"Сотрудник {eid}"
            p = Person(eid, name)
            p.employee = True
            self.persons[eid] = p
        return p

    def _bind_employee(self, tr, eid, now, size, live_ids):
        """Закрепить трек за опознанным сотрудником (с переносом уже накопленной истории)."""
        cur_pid = self.track_to_person.get(tr.id)
        if cur_pid == eid:
            return
        p = self._employee_person(eid)
        if p.track_id is not None and p.track_id != tr.id and p.track_id in live_ids:
            return                                  # этот сотрудник уже ведётся другим треком
        cur = self.persons.get(cur_pid) if cur_pid is not None else None
        if cur is not None and cur.employee:        # трек раньше считали другим сотрудником — отцепляем
            cur.track_id = None
            self._mark_absent(cur, tr, size)
            cur = None
        if p.state in ABSENT_STATES:                # сотрудник вернулся
            if p.row_id is not None:
                self.storage.touch_interval(p.row_id, tr.first_seen)
            p.row_id, p.state, p.zone = None, None, None
            p.absent_since, p.exit_zone = None, None
        if cur is not None:                         # анонимная запись -> настоящему сотруднику
            self.storage.merge_person(cur.id, p.id)
            if p.state is None:
                p.row_id, p.state, p.zone, p.since = cur.row_id, cur.state, cur.zone, cur.since
                p.cand, p.force_commit = cur.cand, cur.state is None
            self.persons.pop(cur.id, None)
        else:
            p.force_commit = True
            p.since = tr.first_seen
        p.track_id = tr.id
        p.cand = None if p.state is None else p.cand
        p.last_touch = 0.0
        self.track_to_person[tr.id] = p.id

    def _mark_absent(self, p, tr, size):
        w, h = size
        ax, ay = tr.anchor()
        pos = (ax / w, ay / h)
        state, zone = self._absence_state(pos)
        p.track_id = None
        p.last_pos = pos
        p.exit_zone = zone
        p.absent_since = tr.last_seen
        p.cand = None
        self._commit(p, state, zone, tr.last_seen)

    # ---------- главный шаг ----------
    def update(self, tracks, expired, now, size):
        c = self.cfg
        live_ids = {tr.id for tr in tracks if tr.visible}
        for tr in expired:
            pid = self.track_to_person.pop(tr.id, None)
            if pid is not None and pid in self.persons:
                p = self.persons[pid]
                if p.track_id == tr.id:
                    self._mark_absent(p, tr, size)

        for tr in tracks:
            if tr.visible and tr.motion_ema >= c["motion_threshold"] and tr.history:
                tr.last_motion_ts = tr.history[-1][0]
            if tr.emp_id is not None and tr.visible and self.track_to_person.get(tr.id) != tr.emp_id:
                self._bind_employee(tr, tr.emp_id, now, size, live_ids)
            elif tr.hits >= 3 and tr.id not in self.track_to_person:
                if self.faces_on and now - tr.first_seen < self.identify_wait:
                    continue                        # даём время опознать по лицу
                if self.faces_on and not self.record_unknown:
                    continue
                self._assign(tr, now, size)

        for tr in tracks:
            pid = self.track_to_person.get(tr.id)
            if pid is None or not tr.visible:
                continue
            p = self.persons[pid]
            state, zone = self._classify(tr, size)
            zname = zone.name if zone is not None else None
            w, h = size
            ax, ay = tr.anchor()
            p.last_pos = (ax / w, ay / h)
            if p.state is None or p.force_commit:
                self._commit(p, state, zone, p.since or now)
                continue
            if state == p.state and zname == p.zone:
                p.cand = None
            elif p.cand is None or p.cand[0] != state or p.cand[1] != zname:
                p.cand = (state, zname, now)
            elif now - p.cand[2] >= c["min_state_seconds"]:
                self._commit(p, state, zone, p.cand[2])

        # поддерживаем интервалы «живыми» и убираем давно отсутствующих
        max_abs = c["max_absence_minutes"] * 60
        for pid in list(self.persons):
            p = self.persons[pid]
            if p.state in ABSENT_STATES and p.absent_since is not None \
                    and now - p.absent_since > max_abs:
                if p.row_id is not None:
                    self.storage.touch_interval(p.row_id, p.absent_since + max_abs)
                del self.persons[pid]
                continue
            if p.row_id is not None and now - p.last_touch >= 5.0:
                self.storage.touch_interval(p.row_id, now)
                p.last_touch = now

    def snapshot(self, now=None):
        now = now or time.time()
        out = []
        for p in self.persons.values():
            if p.state is None:
                continue
            out.append({
                "person_id": p.id, "name": p.name, "state": p.state,
                "label": STATE_LABELS.get(p.state, p.state), "zone": p.zone,
                "duration": max(0.0, now - p.since), "track_id": p.track_id,
                "employee": p.employee,
            })
        return out
