"""Rule layer that turns an occupancy probability into appliance commands.

The ML model answers "will anybody be here?". This module answers "so what
should be ON?", adding comfort rules, hysteresis (to stop relays chattering)
and safety rules (never cut power on a room that is probably in use).
"""
import config as C


def decide(prob, f, prev, room):
    """
    prob : P(occupied next interval) from the Random Forest
    f    : dict with temp, humidity, lux, mins_since_motion, motion_ratio_15m, scheduled_now
    prev : dict with the current light/fan/ac state (0/1)
    room : classroom row (ac_w > 0 means the room has an air conditioner)
    returns (light, fan, ac, occupied, reason)
    """
    pct = round(prob * 100)
    idle = f["mins_since_motion"]
    pir = f.get("pir", 0)

    # 1) Confidently empty / No person detected by sensor -> lights and appliances OFF automatically
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
        return prev["light"], prev["fan"], prev["ac"], 0, why

    # 3) Occupied -> switch only what comfort needs
    t, h, lux = f["temp"], f["humidity"], f["lux"]
    if prev["light"]:
        light = 0 if lux >= C.LUX_OFF else 1
    else:
        light = 1 if lux < C.LUX_ON else 0

    ac = 0
    if room["ac_w"] > 0 and prob >= C.P_AC_ALLOWED and (f["scheduled_now"] or f["motion_ratio_15m"] >= 0.5):
        ac = 1 if (t >= C.AC_ON_TEMP or (prev["ac"] and t >= C.AC_HOLD_TEMP)) else 0

    fan = 0
    if not ac:
        fan = 1 if (t >= C.FAN_ON_TEMP or h >= C.FAN_ON_HUMIDITY or (prev["fan"] and t >= C.FAN_HOLD_TEMP)) else 0

    parts = []
    parts.append("lights ON (dim room)" if light else "lights OFF (daylight is enough)")
    parts.append("AC ON (hot, class confirmed)" if ac else ("fan ON" if fan else "no cooling needed"))
    source = "class in session" if f["scheduled_now"] else "motion detected"
    return light, fan, ac, 1, f"Occupied ({pct}%, {source}) - " + ", ".join(parts)
