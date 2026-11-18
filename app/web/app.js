"use strict";
const $ = (s) => document.querySelector(s);
const api = async (url, opt) => {
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error(r.status);
  return r.json();
};
const post = (url, body) => api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const toast = (m) => { const t = $("#toast"); t.textContent = m; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2200); };

const COLORS = {
  working: "#16a34a", sitting_idle: "#d97706", at_desk_standing: "#65a30d", approach_desk: "#0891b2",
  at_shelf: "#2563eb", approach_shelf: "#7c3aed", rest: "#db2777", walking: "#64748b",
  standing: "#94a3b8", sitting: "#a3a380", away_restroom: "#dc2626", away_exit: "#9f1239", away_unknown: "#475569",
};
const ZONE_COLORS = {desk: "#16a34a", shelf: "#2563eb", rest: "#db2777", restroom: "#dc2626", exit: "#9f1239"};
let META = {states: {}, zone_kinds: {}};
let CFG = null;
let zones = [];

const fmt = (sec) => {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h}ч ${m}м` : m ? `${m}м ${s}с` : `${s}с`;
};
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

// ---------- вкладки ----------
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "report") loadReport();
  if (b.dataset.tab === "zones") { loadZoneFrame(); drawZones(); }
  if (b.dataset.tab === "staff") loadStaff();
  if (b.dataset.tab === "settings") fillSettings();
  startVideo();
}));

// ---------- ОНЛАЙН ----------
function startVideo() {
  const live = $("#tab-live").classList.contains("active");
  const img = $("#video");
  if (live && !img.src.includes("video.mjpg")) img.src = "/video.mjpg?" + Date.now();
  if (!live && img.src.includes("video.mjpg")) img.removeAttribute("src");   // не держим поток, когда вкладка скрыта
}

function drawOverlay(people) {
  const img = $("#video"), cv = $("#overlay");
  const w = img.clientWidth, h = img.clientHeight;
  if (!w) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.lineWidth = 1.5; ctx.font = "13px Segoe UI, sans-serif";
  for (const z of zones) {
    if (z.points.length < 3) continue;
    ctx.strokeStyle = ZONE_COLORS[z.kind] || "#fff";
    ctx.fillStyle = (ZONE_COLORS[z.kind] || "#fff") + "22";
    ctx.beginPath();
    z.points.forEach((p, i) => i ? ctx.lineTo(p[0] * w, p[1] * h) : ctx.moveTo(p[0] * w, p[1] * h));
    ctx.closePath(); ctx.fill(); ctx.stroke();
  }
  ctx.lineWidth = 2.5;
  for (const p of people) {
    const [x1, y1, x2, y2] = p.box, c = COLORS[p.state] || "#fff";
    ctx.strokeStyle = c;
    ctx.strokeRect(x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h);
    const text = `${p.employee ? "" : "? "}${p.name}: ${p.label}`;
    const tw = ctx.measureText(text).width + 10;
    ctx.fillStyle = c;
    ctx.fillRect(x1 * w, Math.max(0, y1 * h - 20), tw, 20);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x1 * w + 5, Math.max(14, y1 * h - 6));
  }
}

async function pollStatus() {
  try {
    const st = await api("/api/status");
    const cam = st.camera;
    const names = {online: "камера онлайн", connecting: "подключение...", error: "нет связи", stopped: "остановлена"};
    $("#hstat").innerHTML = `<span><span class="dot ${cam.status}"></span>${names[cam.status] || cam.status}</span>` +
      `<span>${esc(st.detector)}</span><span>${st.fps} к/с</span>`;
    const nv = $("#novideo");
    nv.classList.toggle("show", cam.status !== "online");
    nv.innerHTML = cam.status === "online" ? "" : `${names[cam.status]}<br><small>${esc(cam.error || "")}</small>`;
    if (st.detector_error) nv.classList.add("show"), nv.innerHTML += `<br><small>${esc(st.detector_error)}</small>`;
    drawOverlay(st.people || []);
    const box = $("#people");
    if (!st.persons.length) box.innerHTML = '<div class="empty">Людей пока не обнаружено</div>';
    else box.innerHTML = st.persons.map((p) =>
      `<div class="person"><span class="swatch" style="background:${COLORS[p.state] || "#999"}"></span>
       <div><div class="pname" data-id="${p.person_id}" title="Нажмите, чтобы переименовать">${esc(p.name)}</div>
       <div class="pstate">${esc(p.label)}${p.zone ? " · " + esc(p.zone) : ""} · ${fmt(p.duration)}</div></div></div>`).join("");
  } catch (e) { /* сервер перезапускается */ }
}
$("#people").addEventListener("click", async (e) => {
  const el = e.target.closest(".pname"); if (!el) return;
  const name = prompt("Имя сотрудника:", el.textContent);
  if (name && name.trim()) { await post(`/api/person/${el.dataset.id}/rename`, {name: name.trim()}); pollStatus(); }
});

// ---------- ОТЧЁТЫ ----------
async function loadReport() {
  const date = $("#rdate").value || today();
  const wd = $("#rworkday").checked ? 1 : 0;
  $("#rcsv").href = `/api/export.csv?date=${date}&workday=${wd}`;
  const [rep, tl] = await Promise.all([api(`/api/report?date=${date}&workday=${wd}`), api(`/api/timeline?date=${date}&workday=${wd}`)]);
  if (!rep.people.length) $("#rtable").innerHTML = '<div class="empty">За этот день данных нет</div>';
  else $("#rtable").innerHTML = '<div class="tablewrap"><table><tr><th>Сотрудник</th><th>В кадре</th><th class="num">Работал</th><th>Доля</th>' +
    '<th class="num">Отсутствовал</th><th class="num">Выходов</th><th class="num">В туалет</th><th>Первое / последнее</th></tr>' +
    rep.people.map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${fmt(p.present_seconds)}</td><td class="num">${fmt(p.work_seconds)}</td>
      <td><div class="bar" title="${p.work_percent}%"><i style="width:${p.work_percent}%"></i></div></td>
      <td class="num">${fmt(p.away_seconds)}</td><td class="num">${p.away_count}</td><td class="num">${p.restroom_count}</td>
      <td>${hhmm(p.first_seen)} – ${hhmm(p.last_seen)}</td></tr>`).join("") + "</table></div>";
  renderTimeline(tl.intervals);
  $("#rlegend").innerHTML = Object.entries(META.states).map(([k, v]) => `<span><i class="swatch" style="background:${COLORS[k]}"></i>${esc(v)}</span>`).join("");
}

function renderTimeline(ivs) {
  const box = $("#rtimeline");
  if (!ivs.length) { box.innerHTML = '<div class="empty">Нет данных</div>'; return; }
  const t0 = Math.floor(Math.min(...ivs.map((i) => i.start)) / 3600) * 3600;
  const t1 = Math.ceil(Math.max(...ivs.map((i) => i.end)) / 3600) * 3600 || t0 + 3600;
  const span = Math.max(3600, t1 - t0);
  const byP = {};
  ivs.forEach((i) => (byP[i.name] = byP[i.name] || []).push(i));
  box.innerHTML = Object.entries(byP).map(([name, list]) =>
    `<div class="tl-row"><div class="tl-name" title="${esc(name)}">${esc(name)}</div><div class="tl-track">` +
    list.map((i) => `<i style="left:${(i.start - t0) / span * 100}%;width:${Math.max(0.15, (i.end - i.start) / span * 100)}%;background:${COLORS[i.state] || "#999"}"
      title="${esc(META.states[i.state] || i.state)}${i.zone ? " · " + esc(i.zone) : ""}\n${hhmm(i.start)}–${hhmm(i.end)} (${fmt(i.end - i.start)})"></i>`).join("") +
    "</div></div>").join("") +
    `<div class="tl-axis"><span>${hhmm(t0)}</span><span>${hhmm(t0 + span / 2)}</span><span>${hhmm(t0 + span)}</span></div>`;
}
$("#rrefresh").onclick = loadReport;
$("#rdate").onchange = loadReport;
$("#rworkday").onchange = loadReport;

// ---------- ЗОНЫ ----------
let drawing = null;   // массив точек новой зоны
const zimg = $("#zimg"), zc = $("#zcanvas");
function loadZoneFrame() { zimg.src = "/api/frame.jpg?" + Date.now(); }
zimg.onload = drawZones;
window.addEventListener("resize", () => { drawZones(); });

function drawZones() {
  const w = zimg.clientWidth, h = zimg.clientHeight;
  if (!w) return;
  zc.width = w; zc.height = h;
  const ctx = zc.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  ctx.font = "13px Segoe UI, sans-serif"; ctx.lineWidth = 2;
  const path = (pts) => { ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0] * w, p[1] * h) : ctx.moveTo(p[0] * w, p[1] * h)); };
  for (const z of zones) {
    const c = ZONE_COLORS[z.kind] || "#fff";
    path(z.points); ctx.closePath();
    ctx.fillStyle = c + "33"; ctx.fill(); ctx.strokeStyle = c; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.strokeStyle = "#000"; ctx.lineWidth = 3;
    const t = z.name + (z.owner ? ` (${z.owner})` : "");
    ctx.strokeText(t, z.points[0][0] * w + 4, z.points[0][1] * h + 14); ctx.fillText(t, z.points[0][0] * w + 4, z.points[0][1] * h + 14);
    ctx.lineWidth = 2;
  }
  if (drawing && drawing.length) {
    ctx.strokeStyle = "#facc15"; ctx.fillStyle = "#facc15";
    path(drawing); ctx.stroke();
    drawing.forEach((p) => { ctx.beginPath(); ctx.arc(p[0] * w, p[1] * h, 4, 0, 7); ctx.fill(); });
  }
}
zc.addEventListener("click", (e) => {
  if (!drawing) return;
  const r = zc.getBoundingClientRect();
  drawing.push([(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]);
  $("#zfinish").disabled = drawing.length < 3;
  drawZones();
});
function setDrawing(on) {
  drawing = on ? [] : null;
  $("#zstart").disabled = on; $("#zfinish").disabled = true; $("#zcancel").disabled = !on;
  drawZones();
}
$("#zstart").onclick = () => setDrawing(true);
$("#zcancel").onclick = () => setDrawing(false);
$("#zfinish").onclick = () => {
  if (!drawing || drawing.length < 3) return;
  const kind = $("#zkind").value;
  const name = $("#zname").value.trim() || `${META.zone_kinds[kind]} ${zones.filter((z) => z.kind === kind).length + 1}`;
  zones.push({id: "z" + Date.now(), name, kind, owner: kind === "desk" ? $("#zowner").value.trim() : "", points: drawing.map((p) => [+p[0].toFixed(4), +p[1].toFixed(4)])});
  $("#zname").value = ""; $("#zowner").value = "";
  setDrawing(false); renderZoneList();
};
function renderZoneList() {
  $("#zlist").innerHTML = zones.length ? zones.map((z, i) =>
    `<div class="zrow"><i class="swatch" style="background:${ZONE_COLORS[z.kind]}"></i><div class="zt">${esc(z.name)}<small>${esc(META.zone_kinds[z.kind])}${z.owner ? " · " + esc(z.owner) : ""}</small></div><button data-del="${i}">Удалить</button></div>`).join("")
    : '<div class="empty">Зон пока нет</div>';
  drawZones();
}
$("#zlist").addEventListener("click", (e) => {
  const b = e.target.closest("[data-del]"); if (!b) return;
  zones.splice(+b.dataset.del, 1); renderZoneList();
});
$("#zrefresh").onclick = loadZoneFrame;
$("#zsave").onclick = async () => { await post("/api/config", {zones}); toast("Зоны сохранены"); };

// ---------- НАСТРОЙКИ ----------
function showSrcGroups() {
  const t = $("#s_type").value;
  $("#g_usb").hidden = t !== "usb"; $("#g_url").hidden = t !== "url"; $("#g_file").hidden = t !== "file";
}
async function scanCams() {
  const sel = $("#s_usb_index");
  sel.innerHTML = "<option>Поиск...</option>";
  try {
    const list = await api("/api/cameras");
    sel.innerHTML = list.length ? list.map((c) => `<option value="${c.index}">${esc(c.name)}</option>`).join("") : '<option value="0">Камеры не найдены</option>';
    if (CFG) sel.value = CFG.source.usb_index;
  } catch (e) { sel.innerHTML = '<option value="0">Ошибка поиска</option>'; }
}
function fillSettings() {
  if (!CFG) return;
  const s = CFG.source, d = CFG.detector, a = CFG.activity;
  $("#s_type").value = s.type; $("#s_url").value = s.url; $("#s_file").value = s.file;
  $("#d_backend").value = d.backend; $("#d_model").value = d.model; $("#d_device").value = String(d.device);
  $("#d_fps").value = d.process_fps; $("#d_conf").value = d.conf;
  $("#a_min").value = a.min_state_seconds; $("#a_idle").value = a.idle_after_seconds; $("#a_lost").value = a.lost_timeout;
  $("#w_start").value = CFG.workday.start; $("#w_end").value = CFG.workday.end;
  const f = CFG.faces;
  $("#f_enabled").checked = f.enabled; $("#f_unknown").checked = f.record_unknown; $("#f_thr").value = f.threshold;
  $("#f_votes").value = f.min_votes; $("#f_wait").value = f.identify_wait; $("#f_min").value = f.min_face_px;
  $("#p_profile").value = (CFG.ui || {}).profile || "standard";
  $("#r_days").value = CFG.storage.retention_days; $("#v_port").value = CFG.server.port; $("#v_pass").value = CFG.server.password;
  showSrcGroups(); scanCams();
}
function collect() {
  return {
    source: {type: $("#s_type").value, usb_index: +($("#s_usb_index").value || 0), url: $("#s_url").value.trim(), file: $("#s_file").value.trim()},
    detector: {backend: $("#d_backend").value, model: $("#d_model").value, device: $("#d_device").value,
      process_fps: +$("#d_fps").value || 5, conf: +$("#d_conf").value || 0.4},
    activity: {min_state_seconds: +$("#a_min").value || 3, idle_after_seconds: +$("#a_idle").value || 45, lost_timeout: +$("#a_lost").value || 8},
    faces: {enabled: $("#f_enabled").checked, record_unknown: $("#f_unknown").checked,
      threshold: +$("#f_thr").value || 0.4, min_votes: +$("#f_votes").value || 2,
      identify_wait: +$("#f_wait").value || 12, min_face_px: +$("#f_min").value || 40},
    workday: {start: $("#w_start").value || "08:00", end: $("#w_end").value || "18:00"},
    storage: {retention_days: +$("#r_days").value || 90},
    server: {port: +$("#v_port").value || 8080, password: $("#v_pass").value},
  };
}
async function saveSettings(msg) {
  await post("/api/config", collect());
  CFG = await api("/api/config");
  toast(msg || "Настройки сохранены");
}
$("#s_type").onchange = showSrcGroups;
$("#s_scan").onclick = scanCams;
$("#s_save").onclick = () => saveSettings();
$("#s_apply_src").onclick = () => saveSettings("Источник применён, переподключаюсь...");

$("#p_apply").onclick = async () => {
  await post("/api/profile", {name: $("#p_profile").value});
  CFG = await api("/api/config"); fillSettings(); toast("Профиль применён");
};

// ---------- СОТРУДНИКИ ----------
let enrollTarget = null;
async function loadStaff() {
  const d = await api("/api/employees");
  const f = d.faces;
  $("#facestatus").innerHTML = !f.enabled ? "Распознавание лиц выключено (Настройки → Распознавание лиц)."
    : f.available ? `Распознавание лиц работает. Лиц в базе у сотрудников: ${f.enrolled}.`
    : `<span style="color:var(--bad)">${esc(f.error)}</span>`;
  $("#stafflist").innerHTML = d.employees.length ? d.employees.map((e) =>
    `<div class="erow"><div class="et"><b>${esc(e.name)}</b><small>${esc(e.position || "—")} · согласие от ${e.consent_ts ? new Date(e.consent_ts * 1000).toLocaleDateString("ru-RU") : "—"}</small></div>
      <span class="badge ${e.faces ? "ok" : "no"}">${e.faces ? "лиц: " + e.faces : "лицо не снято"}</span>
      <div class="ea"><button data-act="cam" data-id="${e.id}">Снять с камеры</button>
      <button data-act="photo" data-id="${e.id}">Загрузить фото</button>
      <button data-act="edit" data-id="${e.id}">Изменить</button>
      <button data-act="revoke" data-id="${e.id}" class="danger">Отозвать согласие</button>
      <button data-act="del" data-id="${e.id}" class="danger">Удалить</button></div></div>`).join("")
    : '<div class="empty">Сотрудников пока нет. Добавьте первого справа.</div>';
}
$("#e_add").onclick = async () => {
  try {
    await post("/api/employees", {name: $("#e_name").value, position: $("#e_pos").value, consent: $("#e_consent").checked});
    $("#e_name").value = ""; $("#e_pos").value = ""; $("#e_consent").checked = false;
    toast("Сотрудник добавлен — теперь снимите лицо"); loadStaff();
  } catch (e) { toast("Укажите ФИО и подтвердите согласие"); }
};
$("#stafflist").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]"); if (!b) return;
  const id = +b.dataset.id, act = b.dataset.act;
  if (act === "cam") {
    b.disabled = true; b.textContent = "Смотрите в камеру...";
    try {
      const r = await post(`/api/employees/${id}/enroll`, {shots: 5});
      toast(r.ok ? `Сохранено лиц: ${r.added}` : "Не получилось: " + r.error);
    } catch (e) { toast("Не получилось: нет лица в кадре или нет камеры"); }
    loadStaff();
  } else if (act === "photo") { enrollTarget = id; $("#e_files").click(); }
  else if (act === "edit") {
    const row = b.closest(".erow"), name = prompt("ФИО:", row.querySelector("b").textContent);
    if (name && name.trim()) { await fetch(`/api/employees/${id}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: name.trim()})}); loadStaff(); }
  } else if (act === "revoke") {
    if (confirm("Удалить сохранённые лица этого сотрудника? Распознавание для него прекратится, история останется.")) { await post(`/api/employees/${id}/revoke`, {}); loadStaff(); }
  } else if (act === "del") {
    const erase = confirm("Удалить сотрудника.\n\nОК — удалить и всю историю активности.\nОтмена — удалить только лицо и имя (история останется обезличенной).");
    if (confirm("Точно удалить сотрудника?")) { await fetch(`/api/employees/${id}?erase=${erase ? 1 : 0}`, {method: "DELETE"}); loadStaff(); }
  }
});
$("#e_files").onchange = async (ev) => {
  const fd = new FormData();
  [...ev.target.files].forEach((f) => fd.append("photos", f));
  ev.target.value = "";
  if (!enrollTarget) return;
  try {
    const r = await (await fetch(`/api/employees/${enrollTarget}/photos`, {method: "POST", body: fd})).json();
    toast(r.ok ? `Сохранено лиц: ${r.added}` : "Не получилось: " + r.error);
  } catch (e) { toast("Ошибка загрузки"); }
  loadStaff();
};

// ---------- старт ----------
(async function init() {
  META = await api("/api/meta");
  $("#zkind").innerHTML = Object.entries(META.zone_kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  CFG = await api("/api/config");
  zones = CFG.zones || [];
  $("#rdate").value = today();
  renderZoneList();
  startVideo();
  pollStatus(); setInterval(pollStatus, 1000);
  setInterval(() => { if ($("#tab-report").classList.contains("active")) loadReport(); }, 30000);
  const first = CFG.source.type === "url" && !CFG.source.url;
  if (first) document.querySelector('[data-tab="settings"]').click();
})();
