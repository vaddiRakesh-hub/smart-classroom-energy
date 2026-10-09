"""Core telemetry ingestion, feature construction, ML inference, and actuation pipeline.

Receives sensor readings from IoT hardware, computes temporal and historical motion
features, queries the occupancy prediction model, executes controller policy, and commits
readings and state transitions atomically.
"""
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
import logging

import config as C
import db
from controller import decide
from exceptions import ValidationError
from ml import predictor
from ml.features import build_vector, motion_features
from timetable import slots_for

logger = logging.getLogger("smart_classroom.service")

_rooms: Dict[int, Dict[str, Any]] = {}
_timetable: List[Dict[str, Any]] = []


def reload_cache() -> None:
    """Reload classroom profiles and timetable schedules into memory cache."""
    global _rooms, _timetable
    _rooms = {r["id"]: r for r in db.q("SELECT * FROM classrooms")}
    _timetable = db.q("SELECT * FROM timetable")
    logger.debug("Loaded %d classrooms and %d timetable slots into cache", len(_rooms), len(_timetable))


def rooms() -> Dict[int, Dict[str, Any]]:
    """Return in-memory map of classroom_id -> classroom metadata dictionary."""
    return _rooms


def now_iso() -> str:
    """Return the current local timestamp formatted as an ISO 8601 string without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


def _abs_min(ts: str) -> int:
    """Convert an ISO timestamp string into continuous absolute minutes from ordinal epoch."""
    dt = datetime.fromisoformat(ts)
    return dt.date().toordinal() * 1440 + dt.hour * 60 + dt.minute


def _baseline_w(room: Mapping[str, Any], dt: datetime, actual_w: float) -> float:
    """Compute baseline power draw under unmanaged conventional operating conditions."""
    full = float(room["light_w"] + room["fan_w"] + room["ac_w"])
    is_open = dt.weekday() in C.OPEN_DAYS and C.OPEN_FROM_H <= dt.hour < C.OPEN_TO_H
    return full if is_open else actual_w


def validate(p: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and sanitize an incoming telemetry payload.

    Args:
        p: Raw telemetry dictionary from client or IoT device.

    Returns:
        Cleaned telemetry dictionary with typed fields.

    Raises:
        ValidationError: If required keys are missing, classroom is unknown,
                         or sensor values violate physical bounds.
    """
    for k in ("classroom_id", "pir", "temp", "humidity", "lux"):
        if k not in p:
            raise ValidationError(f"missing field '{k}'")
    try:
        p["classroom_id"] = int(p["classroom_id"])
    except (ValueError, TypeError):
        raise ValidationError("invalid classroom_id format")

    if p["classroom_id"] not in _rooms:
        raise ValidationError("unknown classroom_id")

    p["pir"] = 1 if int(p["pir"]) else 0
    try:
        for k in ("temp", "humidity", "lux"):
            p[k] = float(p[k])
    except (ValueError, TypeError):
        raise ValidationError("sensor metrics must be numeric")

    if not (-10.0 <= p["temp"] <= 70.0 and 0.0 <= p["humidity"] <= 100.0 and 0.0 <= p["lux"] <= 120000.0):
        raise ValidationError("sensor value out of range")

    p["interval_min"] = float(p.get("interval_min", C.STEP_MIN))
    p["ts"] = p.get("ts") or now_iso()
    return p


def process_batch(payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process a batch of telemetry reports through feature extraction, ML prediction, and control.

    Args:
        payloads: List of raw telemetry dictionaries.

    Returns:
        List of actuation command dictionaries for each respective room.
    """
    cleaned_payloads = [validate(dict(p)) for p in payloads]
    vectors, ctx = [], []
    for p in cleaned_payloads:
        room = _rooms[p["classroom_id"]]
        dt = datetime.fromisoformat(p["ts"])
        minute = dt.hour * 60 + dt.minute
        hist = db.q("SELECT ts,pir FROM readings WHERE classroom_id=? AND ts<? ORDER BY ts DESC LIMIT 14",
                    (room["id"], p["ts"]))
        history = [(_abs_min(h["ts"]), h["pir"]) for h in hist]
        ratio, since = motion_features(history, _abs_min(p["ts"]), p["pir"])
        slots = slots_for(_timetable, room["id"], dt.weekday(), room["capacity"])
        vectors.append(build_vector(minute, dt.weekday(), slots, p["pir"], p["temp"], p["humidity"],
                                    p["lux"], ratio, since))
        ctx.append((room, dt, dict(temp=p["temp"], humidity=p["humidity"], lux=p["lux"],
                                   mins_since_motion=since, motion_ratio_15m=ratio,
                                   scheduled_now=vectors[-1][2], pir=p["pir"])))
    probs = predictor.predict(vectors)

    out = []
    with db.transaction():
        for p, (room, dt, f), prob in zip(cleaned_payloads, ctx, probs):
            st = db.one("SELECT * FROM state WHERE classroom_id=?", (room["id"],))
            prev = {k: st[k] for k in ("light", "fan", "ac")}
            if st["mode"] == "manual":
                light, fan, ac = prev["light"], prev["fan"], prev["ac"]
                occupied = 1 if prob >= C.P_OCCUPIED else 0
                reason = "Manual override - automation paused"
            else:
                light, fan, ac, occupied, reason = decide(prob, f, prev, room)
            if not room["ac_w"]:
                ac = 0
            power = light * room["light_w"] + fan * room["fan_w"] + ac * room["ac_w"]
            db.x("INSERT INTO readings (classroom_id,ts,pir,temp,humidity,lux,prob,light,fan,ac,power_w,baseline_w,interval_min)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (room["id"], p["ts"], p["pir"], p["temp"], p["humidity"], p["lux"], round(prob, 4),
                  light, fan, ac, power, _baseline_w(room, dt, power), p["interval_min"]))
            db.x("UPDATE state SET ts=?,light=?,fan=?,ac=?,prob=?,occupied=?,reason=?,pir=?,temp=?,humidity=?,lux=?,power_w=? "
                 "WHERE classroom_id=?",
                 (p["ts"], light, fan, ac, round(prob, 4), occupied, reason, p["pir"], p["temp"],
                  p["humidity"], p["lux"], power, room["id"]))
            changes = [f"{n} {'ON' if new else 'OFF'}" for n, old, new in
                       (("Lights", prev["light"], light), ("Fan", prev["fan"], fan), ("AC", prev["ac"], ac)) if old != new]
            if changes:
                db.x("INSERT INTO events (ts,classroom_id,kind,message) VALUES (?,?,?,?)",
                     (p["ts"], room["id"], "switch", ", ".join(changes) + " - " + reason))
            out.append(dict(classroom_id=room["id"], light=light, fan=fan, ac=ac, mode=st["mode"],
                            probability=round(prob, 3), occupied=occupied, reason=reason,
                            interval_s=int(p["interval_min"] * 60)))
    return out


def process_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single telemetry report and return its actuation command dictionary."""
    return process_batch([payload])[0]
