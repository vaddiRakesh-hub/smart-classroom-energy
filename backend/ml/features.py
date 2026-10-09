"""Feature engineering shared by training and live prediction."""

FEATURES = [
    "hour_frac",            # time of day in hours (0-24)
    "dow",                  # day of week (0=Mon)
    "scheduled_now",        # 1 if a timetabled class is running (or starts within 10 min)
    "mins_to_next_class",   # capped at 120
    "mins_since_class_end", # capped at 120
    "expected_ratio",       # expected students / capacity for the running class
    "pir",                  # PIR motion latched during the last interval
    "motion_ratio_15m",     # share of PIR reports with motion in the last 15 minutes
    "mins_since_motion",    # capped at 60
    "lux",
    "temp",
    "humidity",
]


def timetable_features(slots, minute):
    """slots: [(start, end, expected_ratio)]; minute: minute of day."""
    scheduled, expected = 0, 0.0
    to_next, since_end = 120, 120
    for s, e, ratio in slots:
        if s - 10 <= minute < e:
            scheduled, expected = 1, max(expected, ratio)
        if minute < s:
            to_next = min(to_next, s - minute)
        if minute >= e:
            since_end = min(since_end, minute - e)
    return scheduled, min(to_next, 120), min(since_end, 120), expected


def motion_features(history, now_abs, pir):
    """history: [(abs_minute, pir)] of earlier reports, newest first."""
    window, since = [pir], (0 if pir else None)
    for t, p in history:
        age = now_abs - t
        if 0 < age <= 15:
            window.append(p)
        if p and since is None:
            since = age
        if age > 60:
            break
    return sum(window) / len(window), min(since if since is not None else 60, 60)


def build_vector(minute, dow, slots, pir, temp, humidity, lux, ratio15, since_motion):
    scheduled, to_next, since_end, expected = timetable_features(slots, minute)
    return [minute / 60.0, dow, scheduled, to_next, since_end, expected,
            pir, ratio15, since_motion, lux, temp, humidity]
