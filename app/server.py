"""Веб-сервер: интерфейс, API, видеопоток MJPEG."""
import hmac
import logging
import os
import time
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory

from .activity import STATE_LABELS, ZONE_KINDS
from .camera import probe_usb_cameras
from .config import DB_PATH, PROFILES, ConfigManager
from .pipeline import Pipeline
from .storage import Storage

log = logging.getLogger("server")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def create_app():
    cm = ConfigManager()
    storage = Storage(DB_PATH)
    pipe = Pipeline(cm, storage)
    pipe.start()

    app = Flask(__name__, static_folder=WEB_DIR, static_url_path="/static")
    app.config["JSON_AS_ASCII"] = False
    app.pipeline = pipe

    # --- необязательный пароль (HTTP Basic, логин admin) ---
    @app.before_request
    def _auth():
        pwd = cm.get()["server"].get("password") or ""
        if not pwd:
            return None
        a = request.authorization
        if a and a.username == "admin" and hmac.compare_digest(a.password or "", pwd):
            return None
        return Response("Нужен пароль", 401, {"WWW-Authenticate": 'Basic realm="Office Analytics"'})

    def clip_for(args):
        if args.get("workday", "1") == "0":
            return None
        wd = cm.get()["workday"]
        return (wd["start"], wd["end"])

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/panel")
    def panel():
        return send_from_directory(WEB_DIR, "panel.html")

    @app.get("/api/meta")
    def meta():
        return jsonify({"states": STATE_LABELS, "zone_kinds": ZONE_KINDS})

    @app.get("/api/status")
    def status():
        return jsonify(pipe.status())

    @app.get("/api/config")
    def get_config():
        cfg = cm.get()
        cfg["server"]["password"] = "***" if cfg["server"].get("password") else ""
        return jsonify(cfg)

    @app.post("/api/config")
    def set_config():
        patch = request.get_json(force=True, silent=True) or {}
        srv = patch.get("server")
        if isinstance(srv, dict) and srv.get("password") == "***":
            srv.pop("password")
        cm.update(patch)
        pipe.apply_config()
        return jsonify({"ok": True})

    @app.get("/api/cameras")
    def cameras():
        cfg = cm.get()["source"]
        cam = pipe.camera
        running_idx = None
        if cam and cfg["type"] == "usb" and cam.status == "online":
            running_idx = int(cfg["usb_index"])
        found = [c for c in probe_usb_cameras() if c["index"] != running_idx]
        if running_idx is not None:
            found.append({"index": running_idx, "name": f"Камера {running_idx} (используется сейчас)"})
        return jsonify(sorted(found, key=lambda c: c["index"]))

    @app.get("/api/frame.jpg")
    def frame():
        data = pipe.get_jpeg(max_w=1280, quality=85)
        if data is None:
            return Response("Нет кадра", 503)
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/video.mjpg")
    def video():
        def gen():
            last = None
            while True:
                data = pipe.get_jpeg()
                if data is None or data is last:
                    time.sleep(0.05)
                    continue
                last = data
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                time.sleep(0.08)
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/report")
    def report():
        date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        unk = request.args.get("unknown", "1") != "0"
        return jsonify({"date": date, "people": storage.report(date, clip_for(request.args), include_unknown=unk)})

    @app.get("/api/timeline")
    def timeline():
        date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        return jsonify({"date": date, "intervals": storage.intervals(date, clip_for(request.args))})

    @app.get("/api/export.csv")
    def export_csv():
        date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        data = storage.export_csv(date, clip_for(request.args))
        return Response(data, mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=activity_{date}.csv"})

    @app.post("/api/person/<int:pid>/rename")
    def rename(pid):
        name = ((request.get_json(force=True, silent=True) or {}).get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "Пустое имя"}), 400
        storage.rename_person(pid, name)
        if pipe.engine and pid in pipe.engine.persons:
            pipe.engine.persons[pid].name = name
        return jsonify({"ok": True})

    # ---------- профили производительности ----------
    @app.post("/api/profile")
    def profile():
        name = (request.get_json(force=True, silent=True) or {}).get("name")
        if name not in PROFILES:
            return jsonify({"ok": False, "error": "Неизвестный профиль"}), 400
        cm.apply_profile(name)
        pipe.apply_config()
        return jsonify({"ok": True})

    # ---------- сотрудники и лица ----------
    def _face_ready():
        fe = pipe.faces
        if fe is None:
            return None, "Распознавание лиц выключено в настройках"
        if not fe.available:
            return None, pipe.faces_error or "Модели лиц недоступны"
        return fe, ""

    def _employee_or_404(pid):
        for e in storage.employees():
            if e["id"] == pid:
                return e
        return None

    @app.get("/api/employees")
    def employees_list():
        return jsonify({"employees": storage.employees(), "faces": pipe.faces_status()})

    @app.post("/api/employees")
    def employees_add():
        d = request.get_json(force=True, silent=True) or {}
        name = (d.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "Укажите ФИО сотрудника"}), 400
        if d.get("consent") is not True:
            return jsonify({"ok": False, "error": "Нужно подтвердить письменное согласие сотрудника "
                                                  "на обработку биометрических данных"}), 400
        pid = storage.add_employee(name, (d.get("position") or "").strip())
        log.info("Добавлен сотрудник id=%s (согласие подтверждено)", pid)
        return jsonify({"ok": True, "id": pid})

    @app.patch("/api/employees/<int:pid>")
    def employees_edit(pid):
        d = request.get_json(force=True, silent=True) or {}
        storage.update_employee(pid, (d.get("name") or "").strip() or None, d.get("position"))
        if pipe.engine and pid in pipe.engine.persons and d.get("name"):
            pipe.engine.persons[pid].name = d["name"].strip()
        return jsonify({"ok": True})

    @app.delete("/api/employees/<int:pid>")
    def employees_delete(pid):
        erase = request.args.get("erase") == "1"
        storage.delete_employee(pid, erase_history=erase)
        if pipe.engine:
            for tid, ppid in list(pipe.engine.track_to_person.items()):
                if ppid == pid:
                    pipe.engine.track_to_person.pop(tid)
            pipe.engine.persons.pop(pid, None)
        for tr in pipe.tracker.tracks.values():
            if tr.emp_id == pid:
                tr.emp_id = None
        pipe.reload_gallery()
        log.info("Сотрудник id=%s удалён (история стёрта: %s)", pid, erase)
        return jsonify({"ok": True})

    @app.post("/api/employees/<int:pid>/revoke")
    def employees_revoke(pid):
        """Отзыв согласия: удаляются все лица (эмбеддинги), распознавание этого человека прекращается."""
        storage.delete_faces(pid)
        for tr in pipe.tracker.tracks.values():
            if tr.emp_id == pid:
                tr.emp_id = None
        pipe.reload_gallery()
        return jsonify({"ok": True})

    def _try_add_face(fe, frame, pid, min_px=60):
        """Вернуть (True, '') или (False, причина). В кадре должно быть ровно одно лицо."""
        rows = [r for r in fe.detect(frame, min_px) if r[14] >= 0.8]
        if not rows:
            return False, "лицо не найдено или слишком мелкое"
        if len(rows) > 1:
            return False, "в кадре больше одного лица"
        vec = fe.embed(frame, rows[0])
        other, _ = pipe.gallery.identify(vec, float(cm.get()["faces"]["threshold"]))
        if other is not None and other != pid:
            return False, "лицо уже зарегистрировано у другого сотрудника"
        storage.add_face(pid, vec, getattr(fe, "model_tag", ""))
        return True, ""

    @app.post("/api/employees/<int:pid>/enroll")
    def employees_enroll(pid):
        emp = _employee_or_404(pid)
        if not emp or not emp["consent_ts"]:
            return jsonify({"ok": False, "error": "Нет сотрудника или согласия"}), 400
        fe, err = _face_ready()
        if fe is None:
            return jsonify({"ok": False, "error": err}), 400
        shots = min(10, max(1, int((request.get_json(force=True, silent=True) or {}).get("shots", 5))))
        added, reasons, last_id = 0, [], -1
        for i in range(shots):
            frame, fid, _ = pipe.camera.read() if pipe.camera else (None, 0, 0)
            if frame is None:
                return jsonify({"ok": False, "error": "Нет изображения с камеры"}), 400
            if fid != last_id:
                last_id = fid
                ok, why = _try_add_face(fe, frame, pid)
                if ok:
                    added += 1
                else:
                    reasons.append(why)
            time.sleep(0.8)
        pipe.reload_gallery()
        msg = "" if added else (max(set(reasons), key=reasons.count) if reasons else "нет кадров")
        return jsonify({"ok": added > 0, "added": added, "skipped": shots - added, "error": msg})

    @app.post("/api/employees/<int:pid>/photos")
    def employees_photos(pid):
        emp = _employee_or_404(pid)
        if not emp or not emp["consent_ts"]:
            return jsonify({"ok": False, "error": "Нет сотрудника или согласия"}), 400
        fe, err = _face_ready()
        if fe is None:
            return jsonify({"ok": False, "error": err}), 400
        added, reasons = 0, []
        for f in request.files.getlist("photos")[:20]:
            img = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                reasons.append("не удалось прочитать файл")
                continue
            ok, why = _try_add_face(fe, img, pid, min_px=80)
            if ok:
                added += 1
            else:
                reasons.append(why)
        pipe.reload_gallery()
        msg = "" if added else (max(set(reasons), key=reasons.count) if reasons else "файлы не переданы")
        return jsonify({"ok": added > 0, "added": added, "error": msg})

    return app
