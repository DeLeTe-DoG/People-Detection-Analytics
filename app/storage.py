"""Хранилище на SQLite: сотрудники, их лица (эмбеддинги), интервалы состояний, отчёты."""
import csv
import io
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import numpy as np

ANON_PREFIX = "Неизвестный #"

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    is_employee INTEGER NOT NULL DEFAULT 0,
    consent_ts REAL,
    position TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    zone TEXT,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    created REAL NOT NULL,
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_int_start ON intervals(start_ts);
CREATE INDEX IF NOT EXISTS ix_int_person ON intervals(person_id);
CREATE INDEX IF NOT EXISTS ix_faces_person ON faces(person_id);
"""

ABSENT_STATES = ("away_restroom", "away_exit", "away_unknown")


def day_bounds(date_str=None):
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


class Storage:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(persons)")}
        for col, ddl in (("is_employee", "INTEGER NOT NULL DEFAULT 0"), ("consent_ts", "REAL"),
                         ("position", "TEXT NOT NULL DEFAULT ''")):
            if col not in cols:                       # база от предыдущей версии
                self._db.execute(f"ALTER TABLE persons ADD COLUMN {col} {ddl}")
        fcols = {r[1] for r in self._db.execute("PRAGMA table_info(faces)")}
        if "model" not in fcols:
            self._db.execute("ALTER TABLE faces ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ---------- люди ----------
    def add_person(self, name=""):
        """Анонимный (неопознанный) человек."""
        with self._lock:
            cur = self._db.execute("INSERT INTO persons(name, created) VALUES (?, ?)", (name or "?", time.time()))
            pid = cur.lastrowid
            if not name:
                self._db.execute("UPDATE persons SET name=? WHERE id=?", (f"{ANON_PREFIX}{pid}", pid))
            self._db.commit()
            return pid

    def add_employee(self, name, position="", consent_ts=None):
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO persons(name, created, is_employee, consent_ts, position) VALUES (?,?,1,?,?)",
                (name, time.time(), consent_ts or time.time(), position))
            self._db.commit()
            return cur.lastrowid

    def rename_person(self, pid, name):
        with self._lock:
            self._db.execute("UPDATE persons SET name=? WHERE id=?", (name, pid))
            self._db.commit()

    def persons(self):
        with self._lock:
            return {r[0]: r[1] for r in self._db.execute("SELECT id,name FROM persons")}

    def person_name(self, pid):
        with self._lock:
            r = self._db.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()
        return r[0] if r else None

    def employees(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT p.id, p.name, p.position, p.consent_ts, "
                "(SELECT COUNT(*) FROM faces f WHERE f.person_id=p.id) "
                "FROM persons p WHERE p.is_employee=1 ORDER BY p.name").fetchall()
        return [{"id": r[0], "name": r[1], "position": r[2], "consent_ts": r[3], "faces": r[4]} for r in rows]

    def update_employee(self, pid, name=None, position=None):
        with self._lock:
            if name:
                self._db.execute("UPDATE persons SET name=? WHERE id=? AND is_employee=1", (name, pid))
            if position is not None:
                self._db.execute("UPDATE persons SET position=? WHERE id=? AND is_employee=1", (position, pid))
            self._db.commit()

    def is_employee(self, pid):
        with self._lock:
            r = self._db.execute("SELECT is_employee FROM persons WHERE id=?", (pid,)).fetchone()
        return bool(r and r[0])

    def merge_person(self, src, dst):
        """Перенести все интервалы анонима src к сотруднику dst и удалить src."""
        with self._lock:
            self._db.execute("UPDATE intervals SET person_id=? WHERE person_id=?", (dst, src))
            self._db.execute("DELETE FROM persons WHERE id=? AND is_employee=0", (src,))
            self._db.commit()

    def delete_employee(self, pid, erase_history=False):
        """Удалить сотрудника и его биометрию. erase_history — стереть и историю активности."""
        with self._lock:
            self._db.execute("DELETE FROM faces WHERE person_id=?", (pid,))
            if erase_history:
                self._db.execute("DELETE FROM intervals WHERE person_id=?", (pid,))
                self._db.execute("DELETE FROM persons WHERE id=?", (pid,))
            else:     # история остаётся, но уже как обезличенная запись
                self._db.execute("UPDATE persons SET is_employee=0, consent_ts=NULL, name=? WHERE id=?",
                                 (f"{ANON_PREFIX}{pid}", pid))
            self._db.commit()

    # ---------- лица ----------
    def add_face(self, pid, vec, model=""):
        """model — какой моделью получен вектор: векторы разных моделей несравнимы."""
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        with self._lock:
            self._db.execute("INSERT INTO faces(person_id, embedding, created, model) VALUES (?,?,?,?)",
                             (pid, v.tobytes(), time.time(), model))
            self._db.commit()

    def delete_faces(self, pid):
        """Отзыв согласия на биометрию: удалить все эмбеддинги сотрудника."""
        with self._lock:
            self._db.execute("DELETE FROM faces WHERE person_id=?", (pid,))
            self._db.execute("UPDATE persons SET consent_ts=NULL WHERE id=?", (pid,))
            self._db.commit()

    def load_gallery(self, model=None):
        """Векторы лиц сотрудников с согласием; model — брать только снятые этой моделью."""
        out = {}
        q = ("SELECT f.person_id, f.embedding FROM faces f JOIN persons p ON p.id=f.person_id "
             "WHERE p.is_employee=1 AND p.consent_ts IS NOT NULL")
        args = ()
        if model is not None:
            q += " AND (f.model=? OR f.model='')"
            args = (model,)
        with self._lock:
            for pid, blob in self._db.execute(q, args):
                out.setdefault(pid, []).append(np.frombuffer(blob, dtype=np.float32))
        return out

    # ---------- интервалы ----------
    def open_interval(self, pid, state, zone, start):
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO intervals(person_id,state,zone,start_ts,end_ts) VALUES (?,?,?,?,?)",
                (pid, state, zone, start, start))
            self._db.commit()
            return cur.lastrowid

    def touch_interval(self, row_id, end):
        with self._lock:
            self._db.execute("UPDATE intervals SET end_ts=? WHERE id=?", (end, row_id))
            self._db.commit()

    def purge_old(self, days):
        cutoff = time.time() - days * 86400
        with self._lock:
            self._db.execute("DELETE FROM intervals WHERE end_ts < ?", (cutoff,))
            self._db.execute("DELETE FROM persons WHERE is_employee=0 AND id NOT IN "
                             "(SELECT DISTINCT person_id FROM intervals)")
            self._db.commit()

    def intervals(self, date_str=None, clip=None):
        """Интервалы за день. clip=("08:00","18:00") — обрезать по рабочему времени."""
        t0, t1 = day_bounds(date_str)
        if clip:
            try:
                base = datetime.fromtimestamp(t0)
                hs, ms = map(int, clip[0].split(":"))
                he, me = map(int, clip[1].split(":"))
                t0, t1 = (base.replace(hour=hs, minute=ms).timestamp(),
                          base.replace(hour=he, minute=me).timestamp())
            except (ValueError, IndexError):
                pass
        with self._lock:
            rows = self._db.execute(
                "SELECT i.id, i.person_id, p.name, i.state, i.zone, i.start_ts, i.end_ts, p.is_employee "
                "FROM intervals i JOIN persons p ON p.id=i.person_id "
                "WHERE i.end_ts > ? AND i.start_ts < ? ORDER BY i.start_ts", (t0, t1)).fetchall()
        out = []
        for r in rows:
            s, e = max(r[5], t0), min(r[6], t1)
            if e > s or r[5] == r[6]:
                out.append({"id": r[0], "person_id": r[1], "name": r[2], "state": r[3], "zone": r[4],
                            "start": s, "end": e, "is_employee": bool(r[7])})
        return out

    def report(self, date_str=None, clip=None, include_unknown=True, include_idle_employees=True):
        """Сводка за день по каждому человеку."""
        people = {}
        for iv in self.intervals(date_str, clip):
            if not iv["is_employee"] and not include_unknown:
                continue
            p = people.setdefault(iv["person_id"], {
                "person_id": iv["person_id"], "name": iv["name"], "is_employee": iv["is_employee"],
                "seconds": {}, "zones": {}, "first_seen": iv["start"], "last_seen": iv["end"],
                "away_count": 0, "restroom_count": 0, "no_data": False,
            })
            dur = max(0.0, iv["end"] - iv["start"])
            p["seconds"][iv["state"]] = p["seconds"].get(iv["state"], 0.0) + dur
            if iv["zone"]:
                p["zones"][iv["zone"]] = p["zones"].get(iv["zone"], 0.0) + dur
            p["first_seen"] = min(p["first_seen"], iv["start"])
            p["last_seen"] = max(p["last_seen"], iv["end"])
            if iv["state"] in ABSENT_STATES:
                p["away_count"] += 1
            if iv["state"] == "away_restroom":
                p["restroom_count"] += 1
        if include_idle_employees:
            for e in self.employees():
                if e["id"] not in people:
                    people[e["id"]] = {"person_id": e["id"], "name": e["name"], "is_employee": True,
                                       "seconds": {}, "zones": {}, "first_seen": 0, "last_seen": 0,
                                       "away_count": 0, "restroom_count": 0, "no_data": True}
        result = []
        for p in people.values():
            sec = p["seconds"]
            present = sum(v for k, v in sec.items() if k not in ABSENT_STATES)
            away = sum(v for k, v in sec.items() if k in ABSENT_STATES)
            work = sec.get("working", 0.0) + sec.get("at_shelf", 0.0)
            p.update(present_seconds=present, away_seconds=away, work_seconds=work,
                     work_percent=round(100.0 * work / present, 1) if present > 0 else 0.0)
            result.append(p)
        result.sort(key=lambda x: (not x["is_employee"], x["name"]))
        return result

    def export_csv(self, date_str=None, clip=None):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Сотрудник", "Опознан", "Состояние", "Зона", "Начало", "Конец", "Длительность, сек"])
        for iv in self.intervals(date_str, clip):
            w.writerow([iv["name"], "да" if iv["is_employee"] else "нет", iv["state"], iv["zone"] or "",
                        datetime.fromtimestamp(iv["start"]).strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.fromtimestamp(iv["end"]).strftime("%Y-%m-%d %H:%M:%S"),
                        round(iv["end"] - iv["start"], 1)])
        return "﻿" + buf.getvalue()   # BOM — чтобы Excel открыл кириллицу
