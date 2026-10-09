/* Smart Classroom Energy - dashboard (vanilla JS, no build step) */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = (path, opts) => fetch(path, opts).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const ICON = {
  light: '<svg viewBox="0 0 24 24"><path d="M9 21h6M10 17.5h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1.1 1.2 1.1 2.2h5c0-1 .5-1.7 1.1-2.2A6 6 0 0 0 12 3z"/></svg>',
  fan: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="1.6"/><path d="M12 10.4C12 6 14 3 16.5 4.2c2.3 1.2.5 4.8-3.4 6.6M13.6 12c4.4 0 7.4 2 6.2 4.5-1.2 2.3-4.8.5-6.6-3.4M12 13.6C12 18 10 21 7.5 19.8c-2.3-1.2-.5-4.8 3.4-6.6M10.4 12C6 12 3 10 4.2 7.5c1.2-2.3 4.8-.5 6.6 3.4"/></svg>',
  ac: '<svg viewBox="0 0 24 24"><path d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9M9.5 4.5 12 6.5l2.5-2M9.5 19.5 12 17.5l2.5 2"/></svg>',
};
const LABEL = { light: "Lights", fan: "Fan", ac: "Air conditioner" };

let selected = null;      // classroom id shown in the drawer
let lastOverview = null;
let drawerBusy = false;

/* ---------- formatting ---------- */
const kw = (w) => (w / 1000).toFixed(w >= 10000 ? 0 : 1);
const hhmm = (iso) => iso.slice(11, 16);
function clockText(iso) {
  const d = new Date(iso);
  return `${DAYS[(d.getDay() + 6) % 7]} ${d.getDate()} ${d.toLocaleString("en", { month: "short" })}, <b>${hhmm(iso)}</b>`;
}

/* ---------- hero ---------- */
function renderHero(o) {
  const t = o.totals;
  $("#clock").innerHTML = (o.simulated ? "Demo simulation &nbsp;|&nbsp; " : "Live &nbsp;|&nbsp; ") + clockText(o.now);
  $("#heroLine").innerHTML = t.full_load_kw
    ? `Using <em>${t.live_power_kw} kW</em> right now instead of ${t.full_load_kw} kW`
    : "No data yet";
  const bar = $("#loadbar");
  bar.innerHTML = o.rooms.map((r) => {
    const full = r.light_w + r.fan_w + r.ac_w;
    const pct = Math.round((r.power_w / full) * 100);
    return `<div class="seg" style="flex:${full}" title="${esc(r.name)}: ${(r.power_w / 1000).toFixed(2)} kW of ${(full / 1000).toFixed(2)} kW">
      <i style="width:${pct}%"></i><span>${esc(r.name)}</span></div>`;
  }).join("");
  $("#loadHint").textContent = `Each block is a classroom. The amber part is the power it draws now; the full block is what it would draw with every light, fan and AC switched on. ${t.occupied_rooms} of ${t.rooms_total} rooms are in use.`;
}

function renderStats(o) {
  const t = o.totals;
  const items = [
    [`${t.saved_kwh} kWh`, `saved today (${t.saved_pct}% less than leaving everything on)`],
    [`Rs ${t.cost_saved_inr.toLocaleString("en-IN")}`, `saved today at Rs ${(t.cost_saved_inr / (t.saved_kwh || 1)).toFixed(0)} per kWh`],
    [`${t.co2_saved_kg} kg`, "CO2 emissions avoided today"],
    [`${t.appliances_on}`, `appliances running across ${t.rooms_total} rooms`],
  ];
  $("#stats").innerHTML = items.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
}

/* ---------- room tiles ---------- */
function applianceIcons(r) {
  return ["light", "fan", "ac"].filter((k) => k !== "ac" || r.has_ac)
    .map((k) => `<span class="ic ${r[k] ? "lit" : ""}" title="${LABEL[k]} ${r[k] ? "on" : "off"}">${ICON[k]}</span>`).join("");
}
function renderRooms(o) {
  $("#rooms").innerHTML = o.rooms.map((r) => {
    const cls = r.class_now
      ? `${esc(r.class_now.subject)}<small>until ${r.class_now.end}, ${r.class_now.students} students</small>`
      : r.next_class ? `No class now<small>Next: ${esc(r.next_class.subject)} at ${r.next_class.start}</small>`
        : `No class scheduled<small>Nothing more today</small>`;
    return `<button class="tile ${r.occupied ? "on" : ""}" data-id="${r.id}" aria-label="${esc(r.name)}, ${r.occupied ? "occupied" : "vacant"}. Open details">
      <div class="row"><span class="name">${esc(r.name)}${r.mode === "manual" ? '<span class="badge">Manual</span>' : ""}</span>
        <span class="state">${r.occupied ? "Occupied" : "Vacant"}</span></div>
      <div class="sub">${esc(r.kind)}, ${esc(r.building)}</div>
      <div class="cls">${cls}</div>
      <div class="appl">${applianceIcons(r)}</div>
      <div class="prob"><span>Chance someone is here</span><span>${Math.round(r.probability * 100)}%</span></div>
      <div class="meter"><i style="width:${Math.round(r.probability * 100)}%"></i></div>
      <div class="sub" style="margin-top:.6rem">${(r.power_w / 1000).toFixed(2)} kW now, ${r.saved_kwh} kWh saved today</div>
    </button>`;
  }).join("");
}

/* ---------- charts ---------- */
function barChart({ labels, a, b, w = 560, h = 190, highlight = -1, sub = [] }) {
  const pad = { l: 34, r: 6, t: 8, b: sub.length ? 40 : 24 };
  const max = Math.max(0.1, ...a, ...b);
  const step = (w - pad.l - pad.r) / labels.length;
  const bw = Math.max(4, step * 0.7);
  const y = (v) => h - pad.b - (v / max) * (h - pad.t - pad.b);
  let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">`;
  for (let i = 0; i <= 3; i++) {
    const v = (max * i) / 3, yy = y(v);
    s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${yy}" y2="${yy}" stroke="#E3EAF0"/><text x="${pad.l - 6}" y="${yy + 4}" text-anchor="end">${v.toFixed(v < 10 ? 1 : 0)}</text>`;
  }
  labels.forEach((lab, i) => {
    const x = pad.l + i * step + (step - bw) / 2;
    if (i === highlight) s += `<rect x="${pad.l + i * step}" y="${pad.t}" width="${step}" height="${h - pad.t - pad.b}" fill="#F5B33526"/>`;
    s += `<rect x="${x}" y="${y(a[i])}" width="${bw}" height="${h - pad.b - y(a[i])}" rx="2" fill="#C9D7E1"/>`;
    s += `<rect x="${x + bw * 0.18}" y="${y(b[i])}" width="${bw * 0.64}" height="${h - pad.b - y(b[i])}" rx="2" fill="#10283A"/>`;
    s += `<text x="${x + bw / 2}" y="${h - pad.b + 14}" text-anchor="middle">${lab}</text>`;
    if (sub[i]) s += `<text x="${x + bw / 2}" y="${h - pad.b + 28}" text-anchor="middle" style="fill:#12846D;font-weight:600">${sub[i]}</text>`;
  });
  return s + "</svg>";
}

function renderHourly(d, nowIso) {
  const hrs = d.hours.filter((x) => x.hour >= 6 && x.hour <= 20);
  const curH = parseInt(nowIso.slice(11, 13), 10);
  $("#hourly").innerHTML = barChart({
    labels: hrs.map((x) => x.hour), a: hrs.map((x) => x.baseline_kwh), b: hrs.map((x) => x.actual_kwh),
    highlight: hrs.findIndex((x) => x.hour === curH),
  });
}

function renderDaily(d) {
  if (!d.days.length) { $("#daily").innerHTML = '<p class="empty">No history yet.</p>'; return; }
  $("#daily").innerHTML = barChart({
    labels: d.days.map((x) => DAYS[x.weekday]), a: d.days.map((x) => x.baseline_kwh), b: d.days.map((x) => x.actual_kwh),
    sub: d.days.map((x) => `-${x.saved_kwh.toFixed(0)}`), w: 560, h: 210,
  });
  $("#projection").textContent = d.avg_saved_kwh_per_working_day
    ? `Average saving on a full working day: ${d.avg_saved_kwh_per_working_day} kWh. Over about 22 working days that is roughly ${d.projected_monthly_saving_kwh} kWh, or Rs ${d.projected_monthly_saving_inr.toLocaleString("en-IN")} a month for these ${lastOverview?.totals.rooms_total ?? ""} rooms. Grey bars show kWh with everything left on; dark bars show kWh used.`
    : "A full working day of data is needed for a monthly estimate.";
}

function renderModel(m) {
  if (!m.random_forest) { $("#model").innerHTML = '<p class="empty">Train the model to see its accuracy.</p>'; return; }
  const rows = [["Random Forest (this system)", m.random_forest.f1, true],
                ["Timetable only", m.baseline_timetable_only.f1], ["Motion sensor only", m.baseline_motion_only.f1]];
  $("#model").innerHTML = `<div class="bars">${rows.map(([n, v, best]) =>
    `<div class="bar ${best ? "best" : ""}"><span>${n}</span><div class="track"><i style="width:${v * 100}%"></i></div><b>${(v * 100).toFixed(0)}%</b></div>`).join("")}</div>`;
  const top = Object.entries(m.feature_importance).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k.replaceAll("_", " ")).join(", ");
  $("#importance").textContent = `Overall accuracy ${(m.random_forest.accuracy * 100).toFixed(1)}%, ROC-AUC ${m.random_forest.roc_auc}. The signals that matter most: ${top}.`;
}

function renderFeed(events) {
  $("#feed").innerHTML = events.length ? events.map((e) =>
    `<li><time>${hhmm(e.ts)}</time><div><b>${esc(e.room)}</b><span>${esc(e.message)}</span></div></li>`).join("")
    : '<li class="empty">No switching yet.</li>';
}

/* ---------- drawer ---------- */
function roomChart(h, nowIso) {
  const w = 440, ht = 150, pad = { l: 8, r: 8, t: 8, b: 22 }, x0 = 6 * 60, x1 = 22 * 60;
  const X = (m) => pad.l + ((m - x0) / (x1 - x0)) * (w - pad.l - pad.r);
  const Y = (p) => ht - pad.b - p * (ht - pad.t - pad.b);
  let s = `<svg class="chart" viewBox="0 0 ${w} ${ht}" role="img" aria-label="Occupancy chance through the day">`;
  h.timetable.forEach((c) => { if (c.end_min > x0 && c.start_min < x1) s += `<rect x="${X(c.start_min)}" y="${pad.t}" width="${X(c.end_min) - X(c.start_min)}" height="${ht - pad.t - pad.b}" fill="#C9D7E1" opacity=".6"/>`; });
  const maxP = Math.max(1, ...h.readings.map((r) => r.baseline_w));
  const pts = h.readings.map((r) => { const m = parseInt(r.ts.slice(11, 13)) * 60 + parseInt(r.ts.slice(14, 16)); return { m, r }; }).filter((p) => p.m >= x0 && p.m < x1);
  if (pts.length) {
    s += `<polyline fill="#F5B33555" stroke="#F5B335" stroke-width="1.5" points="${X(pts[0].m)},${Y(0)} ${pts.map((p) => `${X(p.m)},${Y(p.r.power_w / maxP * 0.9)}`).join(" ")} ${X(pts[pts.length - 1].m)},${Y(0)}"/>`;
    s += `<polyline fill="none" stroke="#10283A" stroke-width="2" points="${pts.map((p) => `${X(p.m)},${Y(p.r.prob)}`).join(" ")}"/>`;
  }
  [h.thresholds.occupied, h.thresholds.vacant].forEach((t) => { s += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${Y(t)}" y2="${Y(t)}" stroke="#D5513F" stroke-dasharray="3 4" stroke-width="1"/>`; });
  for (let hr = 6; hr <= 22; hr += 2) s += `<text x="${X(hr * 60)}" y="${ht - 6}" text-anchor="middle">${hr}</text>`;
  return s + "</svg>";
}

async function openDrawer(id) {
  selected = id;
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  await renderDrawer();
  $("#drawer .close")?.focus();
}
function closeDrawer() {
  selected = null;
  $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}

async function renderDrawer() {
  if (selected == null || !lastOverview || drawerBusy) return;
  const r = lastOverview.rooms.find((x) => x.id === selected);
  const h = await api(`/api/classrooms/${selected}/history`);
  const manual = r.mode === "manual";
  const toggles = ["light", "fan", "ac"].filter((k) => k !== "ac" || r.has_ac).map((k) =>
    `<button class="toggle" data-app="${k}" aria-pressed="${r[k] ? "true" : "false"}">${ICON[k]}${LABEL[k]}</button>`).join("");
  $("#drawer").innerHTML = `
    <div class="dh"><div><h2>${esc(r.name)}</h2><div class="sub" style="color:var(--muted)">${esc(r.kind)}, ${esc(r.building)}, seats ${r.capacity}</div></div>
      <button class="close" aria-label="Close details">&times;</button></div>
    <div class="why"><b>${r.occupied ? "Occupied" : "Vacant"}.</b> ${esc(r.reason)}</div>
    <div class="sens">
      <div><b>${r.temp}&deg;C</b><span>Temperature</span></div><div><b>${r.humidity}%</b><span>Humidity</span></div>
      <div><b>${Math.round(r.lux)}</b><span>Light (lux)</span></div><div><b>${r.pir ? "Yes" : "No"}</b><span>Motion seen</span></div>
    </div>
    <h3>Occupancy chance and power today</h3>
    ${roomChart(h, lastOverview.now)}
    <div class="legend"><span class="sw" style="background:#10283A"></span>Chance someone is here
      <span class="sw" style="background:#F5B335"></span>Power used <span class="sw" style="background:#C9D7E1"></span>Timetabled class</div>
    <h3>Control</h3>
    <div class="seg-btn" role="group" aria-label="Control mode">
      <button data-mode="auto" aria-pressed="${!manual}">Automatic</button><button data-mode="manual" aria-pressed="${manual}">Manual</button></div>
    <div class="sw-row">${toggles}</div>
    <p class="hint">${manual ? "Automation is paused for this room. Switch back to Automatic to let the system decide again." : "Tap an appliance to take manual control of this room."}</p>
    <h3>Timetable today</h3>
    <ul class="sched">${h.timetable.length ? h.timetable.map((c) => {
      const f = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
      return `<li><span>${esc(c.subject)}</span><span>${f(c.start_min)} to ${f(c.end_min)}</span></li>`; }).join("") : '<li class="empty">No classes today.</li>'}</ul>`;
}

async function sendOverride(body) {
  drawerBusy = true;
  try { await api(`/api/classrooms/${selected}/override`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  finally { drawerBusy = false; }
  await refresh(true);
}

document.addEventListener("click", (e) => {
  const tile = e.target.closest(".tile");
  if (tile) return openDrawer(+tile.dataset.id);
  if (e.target.closest(".close") || e.target.id === "scrim") return closeDrawer();
  const mode = e.target.closest("[data-mode]");
  if (mode) {
    const r = lastOverview.rooms.find((x) => x.id === selected);
    return sendOverride(mode.dataset.mode === "auto" ? { mode: "auto" } : { mode: "manual", light: r.light, fan: r.fan, ac: r.ac });
  }
  const tg = e.target.closest("[data-app]");
  if (tg) {
    const r = lastOverview.rooms.find((x) => x.id === selected);
    const next = { mode: "manual", light: r.light, fan: r.fan, ac: r.ac };
    next[tg.dataset.app] = r[tg.dataset.app] ? 0 : 1;
    return sendOverride(next);
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && selected != null) closeDrawer(); });

/* ---------- main loop ---------- */
async function refresh(full) {
  try {
    const o = await api("/api/overview");
    lastOverview = o;
    renderHero(o); renderStats(o); renderRooms(o); renderFeed(o.events);
    const typing = $("#drawer").contains(document.activeElement) && !full;   // don't steal focus while a control is focused
    const [hr] = await Promise.all([api("/api/energy/hourly"), typing ? null : renderDrawer()]);
    renderHourly(hr, o.now);
  } catch (err) {
    $("#clock").textContent = "Cannot reach the server. Is backend/app.py running?";
  }
}
async function slowRefresh() {
  try { renderDaily(await api("/api/energy/daily?days=7")); } catch (_) {}
}
(async () => {
  try { renderModel(await api("/api/model")); } catch (_) {}
  await refresh(); await slowRefresh();
  setInterval(refresh, 2500);
  setInterval(slowRefresh, 15000);
})();
