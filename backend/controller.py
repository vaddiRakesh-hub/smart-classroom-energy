"""Rule and comfort arbitration layer for classroom appliance automation.

The ML Random Forest model estimates occupancy probability P(occupied). This
module translates that probability into deterministic appliance relay states (0/1),
applying safety grace periods, comfort temperature/lux hysteresis, and sensor-driven
vacancy shutoff policies.
"""
from typing import Any, Dict, Mapping, Tuple

import config as C


def decide(
    prob: float,
    f: Mapping[str, Any],
    prev: Mapping[str, int],
    room: Mapping[str, Any],
) -> Tuple[int, int, int, int, str]:
    """Arbitrate appliance actuation from predicted occupancy and ambient telemetry.

    Args:
        prob: Estimated occupancy probability P(occupied) in [0.0, 1.0].
        f: Feature dictionary containing 'temp', 'humidity', 'lux',
           'mins_since_motion', 'motion_ratio_15m', 'scheduled_now', and optional 'pir'.
        prev: Current appliance states with keys 'light', 'fan', 'ac' mapped to 0 or 1.
        room: Classroom profile dictionary with keys 'id', 'name', 'ac_w', etc.

    Returns:
        A 5-tuple of (light, fan, ac, occupied, reason) where:
            light, fan, ac: 0 (OFF) or 1 (ON)
            occupied: 0 (vacant) or 1 (occupied)
            reason: Human-readable explanation of the automated decision.
    """
    pct = round(prob * 100)
    idle = float(f["mins_since_motion"])
    pir = int(f.get("pir", 0))

    # 1) Confidently empty / Sensor vacancy detection -> lights and appliances OFF automatically
    if (prob <= C.P_VACANT and idle >= C.GRACE_MIN) or (pir == 0 and idle >= C.GRACE_MIN):
        why = f"Empty room - No person detected by sensor (PIR=0, {int(idle)} min idle) - lights and appliances turned OFF automatically"
        return 0, 0, 0, 0, why

    if pir == 0 and not f["scheduled_now"] and prob <= C.P_VACANT:
        why = f"Empty room - No person detected by sensor ({pct}% occupancy chance) - lights turned OFF automatically"
        return 0, 0, 0, 0, why

    # 2) Uncertain or in Grace Period -> keep what we have (never switch off a room that may be in use)
    if prob < C.P_OCCUPIED and not pir:
        if prob <= C.P_VACANT:
            why = f"Grace period ({pct}% chance, idle for {int(idle)}/{C.GRACE_MIN} min) - holding state"
        else:
            why = f"Uncertain ({pct}%) - holding current state"
        return int(prev["light"]), int(prev["fan"]), int(prev["ac"]), 0, why

    # 3) Occupied -> switch only what comfort needs
    t, h, lux = float(f["temp"]), float(f["humidity"]), float(f["lux"])
    if prev["light"]:
        light = 0 if lux >= C.LUX_OFF else 1
    else:
        light = 1 if lux < C.LUX_ON else 0

    ac = 0
    if room.get("ac_w", 0) > 0 and prob >= C.P_AC_ALLOWED and (f["scheduled_now"] or f["motion_ratio_15m"] >= 0.5):
        ac = 1 if (t >= C.AC_ON_TEMP or (prev["ac"] and t >= C.AC_HOLD_TEMP)) else 0

    fan = 0
    if not ac:
        fan = 1 if (t >= C.FAN_ON_TEMP or h >= C.FAN_ON_HUMIDITY or (prev["fan"] and t >= C.FAN_HOLD_TEMP)) else 0

    parts = []
    parts.append("lights ON (dim room)" if light else "lights OFF (daylight is enough)")
    parts.append("AC ON (hot, class confirmed)" if ac else ("fan ON" if fan else "no cooling needed"))
    source = "class in session" if f["scheduled_now"] else "motion detected"
    return light, fan, ac, 1, f"Occupied ({pct}%, {source}) - " + ", ".join(parts)
