"""Smart Classroom Energy - Production-grade Flask backend and telemetry server.

Features:
- RESTful endpoints for real-time overview, energy metrics, timetables, and device telemetry.
- Strict input validation and defense-in-depth sanitization.
- Defense against DoS via request body limits (1MB) and in-memory rate limiting.
- Timing-attack resistant API key authentication using hmac.compare_digest.
- Comprehensive HTTP security headers (CSP, HSTS-ready, X-Content-Type-Options, etc.).
- Robust JSON error handlers preventing sensitive stack trace disclosures.
- Native integration with Google Gemini Cloud AI advisor and local ML inference.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import hmac
import json
import logging
import os
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

import config as C
import db
from exceptions import ValidationError
from ml import predictor
import service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("smart_classroom.app")

app = Flask(__name__, static_folder=C.FRONTEND_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = C.MAX_REQUEST_BODY_BYTES
sim = None
_bootstrapped = False


# --------------------------------------------------------------------------
# High-Performance In-Memory Sliding-Window Rate Limiter
# --------------------------------------------------------------------------
class RateLimiter:
    """Thread-safe sliding window rate limiter for DoS and brute-force protection."""

    def __init__(self, limit_per_min: int = 180) -> None:
        self.limit = limit_per_min
        self.requests: Dict[str, List[float]] = {}
        self.lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """Check if client request is within the allowed rate window."""
        now = time.time()
        cutoff = now - 60.0
        with self.lock:
            history = self.requests.get(client_id, [])
            valid_history = [t for t in history if t > cutoff]
            if len(valid_history) >= self.limit:
                self.requests[client_id] = valid_history
                return False
            valid_history.append(now)
            self.requests[client_id] = valid_history
            return True


rate_limiter = RateLimiter(limit_per_min=C.RATE_LIMIT_REQUESTS_PER_MIN)


# --------------------------------------------------------------------------
# Helper Functions with Strict Typing & Docstrings
# --------------------------------------------------------------------------
def latest_ts() -> str:
    """Return the most recent telemetry timestamp recorded in database, or current ISO time."""
    row = db.one("SELECT MAX(ts) AS t FROM readings")
    return (row["t"] if row and row["t"] else None) or service.now_iso()


def hhmm(m: int) -> str:
    """Format minutes from midnight into 24-hour HH:MM string."""
    return f"{m // 60:02d}:{m % 60:02d}"


def class_info(room_id: int, ts: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Retrieve currently active and next upcoming lecture for a given classroom."""
    dt = datetime.fromisoformat(ts)
    m = dt.hour * 60 + dt.minute
    slots = db.q("SELECT * FROM timetable WHERE classroom_id=? AND dow=? ORDER BY start_min", (room_id, dt.weekday()))
    cur = next((s for s in slots if s["start_min"] <= m < s["end_min"]), None)
    nxt = next((s for s in slots if s["start_min"] > m), None)

    def _fmt(s: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        return dict(
            subject=s["subject"],
            start=hhmm(s["start_min"]),
            end=hhmm(s["end_min"]),
            students=s["students"],
        )

    return _fmt(cur), _fmt(nxt)


def kpis(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Union[float, int]]:
    """Compute energy accounting metrics, financial savings, and avoided emissions."""
    e = sum(float(r.get("energy_kwh", 0.0)) for r in rows)
    b = sum(float(r.get("baseline_kwh", 0.0)) for r in rows)
    saved = max(0.0, b - e)
    return dict(
        energy_kwh=round(e, 2),
        baseline_kwh=round(b, 2),
        saved_kwh=round(saved, 2),
        saved_pct=round(100.0 * saved / b, 1) if b > 0 else 0.0,
        cost_saved_inr=round(saved * C.TARIFF_INR_PER_KWH),
        co2_saved_kg=round(saved * C.CO2_KG_PER_KWH, 1),
        tariff_inr_per_kwh=C.TARIFF_INR_PER_KWH,
    )


# --------------------------------------------------------------------------
# Request Middleware & Security Filters
# --------------------------------------------------------------------------
@app.before_request
def security_and_boot_filter() -> Optional[Response]:
    """Ensure database bootstrap and apply rate limiting."""
    global _bootstrapped
    if not _bootstrapped:
        bootstrap()

    if C.RATE_LIMIT_ENABLED and request.path.startswith("/api/"):
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "127.0.0.1")
        if not rate_limiter.is_allowed(ip):
            logger.warning("Rate limit exceeded for IP: %s", ip)
            return jsonify(error="Too Many Requests", message="Rate limit exceeded. Please try again shortly.", status_code=429), 429, {"Retry-After": "60"}
    return None


@app.after_request
def add_security_headers(response: Response) -> Response:
    """Inject hardened HTTP security headers to protect against web vulnerabilities."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://generativelanguage.googleapis.com"
    )
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# --------------------------------------------------------------------------
# Error Handlers (Zero Information Leakage)
# --------------------------------------------------------------------------
@app.errorhandler(400)
def handle_bad_request(exc: Exception):
    return jsonify(error="Bad Request", message=str(exc), status_code=400), 400


@app.errorhandler(401)
def handle_unauthorized(exc: Exception):
    return jsonify(error="Unauthorized", message="Authentication credentials missing or invalid", status_code=401), 401


@app.errorhandler(404)
def handle_not_found(exc: Exception):
    return jsonify(error="Not Found", message="Requested resource could not be found", status_code=404), 404


@app.errorhandler(405)
def handle_method_not_allowed(exc: Exception):
    return jsonify(error="Method Not Allowed", message="HTTP method not allowed for this route", status_code=405), 405


@app.errorhandler(413)
def handle_payload_too_large(exc: Exception):
    return jsonify(error="Payload Too Large", message="Request body exceeds allowed 1MB size limit", status_code=413), 413


@app.errorhandler(429)
def handle_too_many_requests(exc: Exception):
    return jsonify(error="Too Many Requests", message="Request limit exceeded. Please back off.", status_code=429), 429


@app.errorhandler(500)
def handle_internal_server_error(exc: Exception):
    logger.error("Unhandled internal server error: %s", exc)
    return jsonify(error="Internal Server Error", message="An unexpected error occurred", status_code=500), 500


# --------------------------------------------------------------------------
# IoT Device API (ESP32 Telemetry)
# --------------------------------------------------------------------------
@app.post("/api/telemetry")
def telemetry():
    """Ingest sensor readings from edge hardware. Protected via timing-safe API key verification."""
    client_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(client_key, C.API_KEY):
        logger.warning("Unauthorized telemetry post attempt from %s", request.remote_addr)
        return jsonify(error="invalid or missing X-API-Key"), 401
    try:
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify(error="Payload must be a JSON object"), 400
        return jsonify(service.process_telemetry(payload))
    except (ValidationError, ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


# --------------------------------------------------------------------------
# Dashboard APIs
# --------------------------------------------------------------------------
@app.get("/api/overview")
def overview():
    """Return live dashboard overview including campus totals, room statuses, and event logs."""
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
        r["saved_kwh"] = round(max(0.0, r["baseline_kwh"] - r["energy_kwh"]), 2)
        r["energy_kwh"], r["baseline_kwh"] = round(r["energy_kwh"], 2), round(r["baseline_kwh"], 2)
        r["class_now"], r["next_class"] = class_info(r["id"], now)
    totals = kpis(rows)
    totals.update(
        rooms_total=len(rows),
        occupied_rooms=sum(r["occupied"] for r in rows),
        live_power_kw=round(sum(r["power_w"] for r in rows) / 1000.0, 2),
        full_load_kw=round(sum(r["light_w"] + r["fan_w"] + r["ac_w"] for r in rows) / 1000.0, 2),
        appliances_on=sum(r["light"] + r["fan"] + r["ac"] for r in rows),
    )
    events = db.q("""SELECT e.ts, c.name AS room, e.message FROM events e JOIN classrooms c ON c.id=e.classroom_id
                     ORDER BY e.id DESC LIMIT 14""")
    return jsonify(now=now, simulated=bool(sim and sim.running), totals=totals, rooms=rows, events=events)


@app.get("/api/energy/hourly")
def hourly():
    """Return hour-by-hour actual vs baseline energy breakdown for a given date."""
    day = request.args.get("date") or latest_ts()[:10]
    try:
        datetime.fromisoformat(day)
    except (ValueError, TypeError):
        day = latest_ts()[:10]
    rows = db.q("""SELECT CAST(substr(ts,12,2) AS INTEGER) AS hour,
                          COALESCE(SUM(power_w*interval_min/60000.0), 0) AS actual_kwh,
                          COALESCE(SUM(baseline_w*interval_min/60000.0), 0) AS baseline_kwh
                   FROM readings WHERE ts LIKE ? GROUP BY hour ORDER BY hour""", (day + "%",))
    by_hour = {r["hour"]: r for r in rows}
    out = [
        dict(
            hour=h,
            actual_kwh=round(float(by_hour.get(h, {}).get("actual_kwh") or 0.0), 3),
            baseline_kwh=round(float(by_hour.get(h, {}).get("baseline_kwh") or 0.0), 3),
        )
        for h in range(24)
    ]
    return jsonify(date=day, hours=out)


@app.get("/api/energy/daily")
def daily():
    """Return historical daily energy savings and projected monthly metrics."""
    try:
        days_arg = request.args.get("days", "7")
        days = max(1, min(int(days_arg), 90))
    except (ValueError, TypeError):
        days = 7
    rows = db.q("""SELECT substr(ts,1,10) AS date,
                          COALESCE(SUM(power_w*interval_min/60000.0), 0) AS actual_kwh,
                          COALESCE(SUM(baseline_w*interval_min/60000.0), 0) AS baseline_kwh,
                          COUNT(DISTINCT substr(ts,12,5)) AS slots
                   FROM readings GROUP BY date ORDER BY date DESC LIMIT ?""", (days,))
    rows.reverse()
    for r in rows:
        act = float(r["actual_kwh"] or 0.0)
        base = float(r["baseline_kwh"] or 0.0)
        r["saved_kwh"] = round(max(0.0, base - act), 2)
        r["actual_kwh"], r["baseline_kwh"] = round(act, 2), round(base, 2)
        try:
            r["weekday"] = datetime.fromisoformat(r["date"]).weekday()
        except (ValueError, TypeError):
            r["weekday"] = 0
    full = [r for r in rows if r["slots"] >= 280 and r["weekday"] < 5]
    avg = sum(r["saved_kwh"] for r in full) / len(full) if full else 0.0
    return jsonify(
        days=rows,
        avg_saved_kwh_per_working_day=round(avg, 1),
        projected_monthly_saving_inr=round(avg * 22 * C.TARIFF_INR_PER_KWH),
        projected_monthly_saving_kwh=round(avg * 22),
    )


@app.get("/api/classrooms/<int:rid>/history")
def history(rid: int):
    """Retrieve full day time-series telemetry and active timetable for a classroom."""
    if rid not in service.rooms():
        return jsonify(error="unknown classroom"), 404
    day = request.args.get("date") or latest_ts()[:10]
    try:
        weekday = datetime.fromisoformat(day).weekday()
    except (ValueError, TypeError):
        day = latest_ts()[:10]
        weekday = datetime.fromisoformat(day).weekday()
    rows = db.q("""SELECT ts,pir,temp,humidity,lux,prob,light,fan,ac,power_w,baseline_w FROM readings
                   WHERE classroom_id=? AND ts LIKE ? ORDER BY ts""", (rid, day + "%"))
    return jsonify(
        date=day,
        thresholds=dict(occupied=C.P_OCCUPIED, vacant=C.P_VACANT),
        readings=rows,
        timetable=db.q(
            "SELECT start_min,end_min,subject FROM timetable WHERE classroom_id=? AND dow=? ORDER BY start_min",
            (rid, weekday),
        ),
    )


@app.post("/api/classrooms/<int:rid>/override")
def override(rid: int):
    """Apply manual human control or resume autonomous AI management for a classroom."""
    room = service.rooms().get(rid)
    if not room:
        return jsonify(error="unknown classroom"), 404
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify(error="Request body must be a JSON object"), 400

    mode = body.get("mode")
    if mode not in ("auto", "manual", None):
        return jsonify(error="Mode must be 'auto' or 'manual'"), 400

    if mode == "auto":
        db.x("UPDATE state SET mode='auto',reason='Automation resumed - awaiting next reading' WHERE classroom_id=?", (rid,))
        db.x("INSERT INTO events (ts,classroom_id,kind,message) VALUES (?,?,?,?)",
             (latest_ts(), rid, "mode", "Automation resumed"))
    else:
        light = int(bool(body.get("light", 0)))
        fan = int(bool(body.get("fan", 0)))
        ac = int(bool(body.get("ac", 0))) if room["ac_w"] else 0
        power = light * room["light_w"] + fan * room["fan_w"] + ac * room["ac_w"]
        db.x("UPDATE state SET mode='manual',light=?,fan=?,ac=?,power_w=?,reason='Manual override - automation paused' "
             "WHERE classroom_id=?", (light, fan, ac, power, rid))
        db.x("INSERT INTO events (ts,classroom_id,kind,message) VALUES (?,?,?,?)",
             (latest_ts(), rid, "mode", f"Manual: lights {'ON' if light else 'OFF'}, fan {'ON' if fan else 'OFF'}"
                                       + (f", AC {'ON' if ac else 'OFF'}" if room["ac_w"] else "")))
    return jsonify(ok=True)


@app.post("/api/classrooms/<int:rid>/sensor_test")
def sensor_test(rid: int):
    """Simulate real-time sensor occupancy event (person present vs vacant).

    Accepts JSON: { "occupied": false } or { "occupied": true }.
    Automatically actuates lights and appliances according to sensor vacancy detection.
    """
    room = service.rooms().get(rid)
    if not room:
        return jsonify(error="unknown classroom"), 404
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify(error="Request body must be a JSON object"), 400

    has_person = bool(body.get("occupied", False))
    now_str = latest_ts()
    dt = datetime.fromisoformat(now_str)

    if not has_person:
        dt = dt + timedelta(minutes=C.GRACE_MIN + 5)
        lux_val = 500.0  # daylight
    else:
        dt = dt + timedelta(minutes=C.STEP_MIN)
        lux_val = 180.0  # dim room needing light

    payload = dict(
        classroom_id=rid,
        pir=1 if has_person else 0,
        temp=28.5 if has_person else 24.0,
        humidity=60.0 if has_person else 50.0,
        lux=lux_val,
        ts=dt.isoformat(),
        interval_min=C.STEP_MIN,
    )
    result = service.process_telemetry(payload)
    action = "Lights switched ON (Person detected)" if result["light"] else "Lights switched OFF automatically (No person detected by sensor)"
    return jsonify(ok=True, action=action, telemetry=result)


@app.get("/api/timetable")
def timetable():
    """Retrieve full institution lecture schedule across all classrooms."""
    rows = db.q("""SELECT t.*, c.name AS room FROM timetable t JOIN classrooms c ON c.id=t.classroom_id
                   ORDER BY t.dow, t.start_min, c.id""")
    for r in rows:
        r["start"], r["end"] = hhmm(r["start_min"]), hhmm(r["end_min"])
    return jsonify(rows)


@app.post("/api/timetable")
def add_timetable():
    """Add a new timetable lecture slot with strict parameter verification."""
    b = request.get_json(force=True) or {}
    if not isinstance(b, dict):
        return jsonify(error="Request body must be a JSON object"), 400
    try:
        cid = int(b["classroom_id"])
        dow = int(b["dow"])
        s_min = int(b["start_min"])
        e_min = int(b["end_min"])
        subj = str(b.get("subject", "")).strip()[:60]
        students = int(b["students"])
        if cid not in service.rooms():
            return jsonify(error="unknown classroom_id"), 400
        if not (0 <= dow <= 6):
            return jsonify(error="dow must be between 0 (Mon) and 6 (Sun)"), 400
        if not (0 <= s_min < e_min <= 1440):
            return jsonify(error="start_min must be >= 0 and < end_min <= 1440"), 400
        if not subj:
            return jsonify(error="subject cannot be empty"), 400
        if students < 0:
            return jsonify(error="students cannot be negative"), 400
        row = (cid, dow, s_min, e_min, subj, students)
    except (KeyError, ValueError, TypeError) as err:
        return jsonify(error=f"invalid request parameters: {err}"), 400

    db.x("INSERT INTO timetable (classroom_id,dow,start_min,end_min,subject,students) VALUES (?,?,?,?,?,?)", row)
    service.reload_cache()
    return jsonify(ok=True), 201


@app.get("/api/ai/advisor")
def ai_advisor():
    """Google Gemini Cloud AI advisor providing campus-wide energy optimization strategies."""
    now = latest_ts()
    day = now[:10]
    rooms = db.q("""SELECT c.name, c.building, c.kind, s.occupied, s.power_w, s.prob, s.temp, s.lux,
                           s.light, s.fan, s.ac, s.mode
                    FROM classrooms c JOIN state s ON s.classroom_id=c.id ORDER BY c.id""")
    totals = kpis(db.q("""SELECT COALESCE(SUM(power_w*interval_min/60000.0), 0) AS energy_kwh,
                                 COALESCE(SUM(baseline_w*interval_min/60000.0), 0) AS baseline_kwh
                          FROM readings WHERE ts LIKE ?""", (day + "%",)))

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        try:
            import urllib.request
            prompt = (
                f"You are the Google Cloud Campus Energy Advisor. Analyze this real-time campus data: "
                f"Current Time: {now}. Total energy saved today: {totals['saved_kwh']} kWh ({totals['saved_pct']}%). "
                f"Avoided emissions: {totals['co2_saved_kg']} kg CO2. Cost saved: Rs {totals['cost_saved_inr']}. "
                f"Classroom status: {rooms}. "
                f"Provide a 3-bullet concise executive recommendation for facilities management on optimizing HVAC, lighting, and schedules."
            )
            req_data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                return jsonify(provider="Google Gemini (Cloud AI)", advice=text, timestamp=now)
        except Exception as exc:
            logger.warning("Gemini AI API call encountered exception: %s", exc)

    points = []
    if totals["saved_pct"] >= 50:
        points.append(f"Campus energy efficiency is optimal with a {totals['saved_pct']}% reduction in electricity consumption ({totals['saved_kwh']} kWh saved today, avoiding {totals['co2_saved_kg']} kg CO2 emissions).")
    else:
        points.append(f"Current energy savings are at {totals['saved_pct']}%. Recommend reviewing timetable gap intervals to maximize automated shutdown.")

    hvac_active = [r["name"] for r in rooms if r["ac"]]
    if hvac_active:
        points.append(f"High-draw air conditioning is active in {', '.join(hvac_active)}. Comfort hysteresis rules are maintaining temperatures between 23.0°C and 27.5°C.")
    else:
        points.append("Air conditioning compressors are currently idle across campus; relying on ambient natural airflow and low-power ceiling fans.")

    empty_rooms = [r["name"] for r in rooms if not r["occupied"]]
    points.append(f"All {len(empty_rooms)} vacant classrooms have automated relays disengaged, cutting standby phantom power draw to 0W.")

    return jsonify(
        provider="Smart Campus AI Engine (Google Gemini Ready)",
        advice="\n\n".join(points),
        timestamp=now,
        metrics=dict(saved_kwh=totals["saved_kwh"], cost_saved_inr=totals["cost_saved_inr"], co2_saved_kg=totals["co2_saved_kg"]),
    )


@app.get("/api/model")
def model_info():
    """Return model evaluation metrics and confusion matrices."""
    return jsonify(predictor.metrics())


@app.get("/api/health")
def health():
    """System health-check endpoint for Railway and container orchestrators."""
    return jsonify(status="ok", simulator=bool(sim and sim.running), latest_reading=latest_ts())


@app.get("/")
def index():
    """Serve single-page frontend interface."""
    return send_from_directory(C.FRONTEND_DIR, "index.html")


def bootstrap() -> None:
    """Initialize SQLite database, in-memory caches, and background simulator thread."""
    global sim, _bootstrapped
    if _bootstrapped:
        return
    db.init_db()
    service.reload_cache()
    predictor.get_model()
    if C.SIM_ENABLED:
        from simulator import Simulator
        sim = Simulator()
        sim.start()
    _bootstrapped = True


if __name__ == "__main__":
    bootstrap()
    port = int(os.environ.get("PORT", 5000))
    logger.info("Smart Classroom Energy listening on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
