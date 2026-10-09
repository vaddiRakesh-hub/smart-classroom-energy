"""Feature engineering shared by model training and real-time live inference.

Transforms raw timestamps, timetable slots, environmental metrics, and PIR history
into continuous normalized feature vectors for the Random Forest occupancy classifier.
"""
from typing import List, Sequence, Tuple

FEATURES: List[str] = [
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


def timetable_features(
    slots: Sequence[Tuple[int, int, float]],
    minute: int,
) -> Tuple[int, int, int, float]:
    """Compute schedule proximity metrics given daily timetable slots and minute of day.

    Args:
        slots: Sequence of (start_min, end_min, expected_student_ratio) tuples.
        minute: Current minute of day [0, 1440).

    Returns:
        Tuple of (scheduled_now, mins_to_next_class, mins_since_class_end, expected_ratio).
    """
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


def motion_features(
    history: Sequence[Tuple[int, int]],
    now_abs: int,
    pir: int,
) -> Tuple[float, float]:
    """Calculate rolling 15-minute motion density and elapsed minutes since last motion.

    Args:
        history: Sequence of (absolute_minute, pir_state) pairs ordered newest first.
        now_abs: Current timestamp in absolute minutes from ordinal epoch.
        pir: Current interval PIR reading (0 or 1).

    Returns:
        Tuple of (motion_ratio_15m, mins_since_motion).
    """
    window: List[int] = [pir]
    since = 0.0 if pir else None
    for t, p in history:
        age = float(now_abs - t)
        if 0 < age <= 15:
            window.append(p)
        if p and since is None:
            since = age
        if age > 60:
            break
    ratio = sum(window) / float(len(window))
    return ratio, min(since if since is not None else 60.0, 60.0)


def build_vector(
    minute: int,
    dow: int,
    slots: Sequence[Tuple[int, int, float]],
    pir: int,
    temp: float,
    humidity: float,
    lux: float,
    ratio15: float,
    since_motion: float,
) -> List[float]:
    """Assemble complete 12-dimensional feature vector for machine learning inference."""
    scheduled, to_next, since_end, expected = timetable_features(slots, minute)
    return [
        minute / 60.0,
        float(dow),
        float(scheduled),
        float(to_next),
        float(since_end),
        float(expected),
        float(pir),
        float(ratio15),
        float(since_motion),
        float(lux),
        float(temp),
        float(humidity),
    ]
