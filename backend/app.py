"""Smart Classroom Energy - Flask backend + dashboard server.

Run:  python app.py        then open http://localhost:5000
"""
import os
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

import config as C
import db
import service
from ml import predictor

app = Flask(__name__, static_folder=C.FRONTEND_DIR, static_url_path="")
sim = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def latest_ts():
    return db.one("SELECT MAX(ts) AS t FROM readings")["t"] or service.now_iso()


def hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def class_info(room_id, ts):
    dt = datetime.fromisoformat(ts)
    m = dt.hour * 60 + dt.minute
    slots = db.q("SELECT * FROM timetable WHERE classroom_id=? AND dow=? ORDER BY start_min", (room_id, dt.weekday()))
    cur = next((s for s in slots if s["start_min"] <= m < s["end_min"]), None)
    nxt = next((s for s in slots if s["start_min"] > m), None)
    fmt = lambda s: None if not s else dict(subject=s["subject"], start=hhmm(s["start_min"]),
                                            end=hhmm(s["end_min"]), students=s["students"])
    return fmt(cur), fmt(nxt)


def kpis(rows):
    e = sum(r["energy_kwh"] for r in rows)
    b = sum(r["baseline_kwh"] for r in rows)
    saved = max(0.0, b - e)
    return dict(energy_kwh=round(e, 2), baseline_kwh=round(b, 2), saved_kwh=round(saved, 2),
                saved_pct=round(100 * saved / b, 1) if b else 0.0,
                cost_saved_inr=round(saved * C.TARIFF_INR_PER_KWH),
                co2_saved_kg=round(saved * C.CO2_KG_PER_KWH, 1))


# --------------------------------------------------------------------------
# device API (ESP32)
# --------------------------------------------------------------------------
@app.post("/api/telemetry")
def telemetry():
    if request.headers.get("X-API-Key") != C.API_KEY:
        return jsonify(error="invalid or missing X-API-Key"), 401
    try:
        return jsonify(service.process_telemetry(request.get_json(force=True)))
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


# --------------------------------------------------------------------------
# dashboard API
# --------------------------------------------------------------------------
@app.get("/api/overview")
def overview():
    now = latest_ts()
    day = now[:10]
    rows = db.q("""
        SELECT c.*, s.ts, s.light, s.fan, s.ac, s.mode, s.prob, s.occupied, s.reason, s.pir,
               s.temp, s.humidity, s.lux, s.power_w,
               COALESCE((SELECT SUM(power_w*interval_min/60000.0) FROM readings r
                         WHERE r.classroom_id=c.id AND r.ts LIKE ?),0) AS energy_kwh,
               COALESCE((SELECT SUM(baseline_w*interval_min/60000.0) FROM readings r
                         WHERE r.classroom_id=c.id AND r.ts LIKE ?),0) AS baseline_kwh
        FROM classrooms c JOIN state s ON s.classroom_id=c.id ORDER BY c.id""", (day + "%", day + "%"))
    for r in rows:
        r["has_ac"] = r["ac_w"] > 0
        r["probability"] = r.pop("prob")
        r["saved_kwh"] = round(max(0, r["baseline_kwh"] - r["energy_kwh"]), 2)
        r["energy_kwh"], r["baseline_kwh"] = round(r["energy_kwh"], 2), round(r["baseline_kwh"], 2)
        r["class_now"], r["next_class"] = class_info(r["id"], now)
    totals = kpis(rows)
    totals.update(rooms_total=len(rows), occupied_rooms=sum(r["occupied"] for r in rows),
                  live_power_kw=round(sum(r["power_w"] for r in rows) / 1000, 2),
                  full_load_kw=round(sum(r["light_w"] + r["fan_w"] + r["ac_w"] for r in rows) / 1000, 2),
                  appliances_on=sum(r["light"] + r["fan"] + r["ac"] for r in rows))
    events = db.q("""SELECT e.ts, c.name AS room, e.message FROM events e JOIN classrooms c ON c.id=e.classroom_id
                     ORDER BY e.id DESC LIMIT 14""")
    return jsonify(now=now, simulated=bool(sim and sim.running), totals=totals, rooms=rows, events=events)


@app.get("/api/energy/hourly")
def hourly():
    day = request.args.get("date") or latest_ts()[:10]
    rows = db.q("""SELECT CAST(substr(ts,12,2) AS INTEGER) AS hour,
                          SUM(power_w*interval_min/60000.0) AS actual_kwh,
                          SUM(baseline_w*interval_min/60000.0) AS baseline_kwh
                   FROM readings WHERE ts LIKE ? GROUP BY hour ORDER BY hour""", (day + "%",))
    by_hour = {r["hour"]: r for r in rows}
    out = [dict(hour=h, actual_kwh=round(by_hour.get(h, {}).get("actual_kwh", 0), 3),
                baseline_kwh=round(by_hour.get(h, {}).get("baseline_kwh", 0), 3)) for h in range(24)]
    return jsonify(date=day, hours=out)


@app.get("/api/energy/daily")
def daily():
    days = min(int(request.args.get("days", 7)), 60)
    rows = db.q("""SELECT substr(ts,1,10) AS date, SUM(power_w*interval_min/60000.0) AS actual_kwh,
                          SUM(baseline_w*interval_min/60000.0) AS baseline_kwh, COUNT(DISTINCT substr(ts,12,5)) AS slots
                   FROM readings GROUP BY date ORDER BY date DESC LIMIT ?""", (days,))
    rows.reverse()
    for r in rows:
        r["saved_kwh"] = round(max(0, r["baseline_kwh"] - r["actual_kwh"]), 2)
        r["actual_kwh"], r["baseline_kwh"] = round(r["actual_kwh"], 2), round(r["baseline_kwh"], 2)
        r["weekday"] = datetime.fromisoformat(r["date"]).weekday()
    full = [r for r in rows if r["slots"] >= 280 and r["weekday"] < 5]      # complete working days only
    avg = sum(r["saved_kwh"] for r in full) / len(full) if full else 0
    return jsonify(days=rows, avg_saved_kwh_per_working_day=round(avg, 1),
                   projected_monthly_saving_inr=round(avg * 22 * C.TARIFF_INR_PER_KWH),
                   projected_monthly_saving_kwh=round(avg * 22))


@app.get("/api/classrooms/<int:rid>/history")
def history(rid):
    day = request.args.get("date") or latest_ts()[:10]
    rows = db.q("""SELECT ts,pir,temp,humidity,lux,prob,light,fan,ac,power_w,baseline_w FROM readings
                   WHERE classroom_id=? AND ts LIKE ? ORDER BY ts""", (rid, day + "%"))
    return jsonify(date=day, thresholds=dict(occupied=C.P_OCCUPIED, vacant=C.P_VACANT), readings=rows,
                   timetable=db.q("SELECT start_min,end_min,subject FROM timetable WHERE classroom_id=? AND dow=? ORDER BY start_min",
                                  (rid, datetime.fromisoformat(day).weekday())))


@app.post("/api/classrooms/<int:rid>/override")
def override(rid):
    body = request.get_json(force=True)
    room = service.rooms().get(rid)
    if not room:
        return jsonify(error="unknown classroom"), 404
    if body.get("mode") == "auto":
        db.x("UPDATE state SET mode='auto' WHERE classroom_id=?", (rid,))
        db.x("INSERT INTO events (ts,classroom_id,kind,message) VALUES (?,?,?,?)",
             (latest_ts(), rid, "mode", "Automation resumed"))
    else:
        light, fan = int(bool(body.get("light"))), int(bool(body.get("fan")))
        ac = int(bool(body.get("ac"))) if room["ac_w"] else 0
        power = light * room["light_w"] + fan * room["fan_w"] + ac * room["ac_w"]
        db.x("UPDATE state SET mode='manual',light=?,fan=?,ac=?,power_w=?,reason='Manual override - automation paused' "
             "WHERE classroom_id=?", (light, fan, ac, power, rid))
        db.x("INSERT INTO events (ts,classroom_id,kind,message) VALUES (?,?,?,?)",
             (latest_ts(), rid, "mode", f"Manual: lights {'ON' if light else 'OFF'}, fan {'ON' if fan else 'OFF'}"
                                       + (f", AC {'ON' if ac else 'OFF'}" if room["ac_w"] else "")))
    return jsonify(ok=True)


@app.get("/api/timetable")
def timetable():
    rows = db.q("""SELECT t.*, c.name AS room FROM timetable t JOIN classrooms c ON c.id=t.classroom_id
                   ORDER BY t.dow, t.start_min, c.id""")
    for r in rows:
        r["start"], r["end"] = hhmm(r["start_min"]), hhmm(r["end_min"])
    return jsonify(rows)


@app.post("/api/timetable")
def add_timetable():
    b = request.get_json(force=True)
    try:
        row = (int(b["classroom_id"]), int(b["dow"]), int(b["start_min"]), int(b["end_min"]),
               str(b["subject"])[:60], int(b["students"]))
    except (KeyError, ValueError):
        return jsonify(error="need classroom_id, dow, start_min, end_min, subject, students"), 400
    db.x("INSERT INTO timetable (classroom_id,dow,start_min,end_min,subject,students) VALUES (?,?,?,?,?,?)", row)
    service.reload_cache()
    return jsonify(ok=True), 201


@app.get("/api/model")
def model_info():
    return jsonify(predictor.metrics())


@app.get("/api/health")
def health():
    return jsonify(status="ok", simulator=bool(sim and sim.running), latest_reading=latest_ts())


@app.get("/")
def index():
    return send_from_directory(C.FRONTEND_DIR, "index.html")


def bootstrap():
    global sim
    db.init_db()
    service.reload_cache()
    predictor.get_model()
    if C.SIM_ENABLED:
        from simulator import Simulator
        sim = Simulator()
        sim.start()


if __name__ == "__main__":
    bootstrap()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Smart Classroom Energy running at http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
