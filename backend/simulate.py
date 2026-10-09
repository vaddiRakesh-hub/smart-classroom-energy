"""World model that produces realistic classroom sensor data.

Used for (1) generating training data and (2) the live demo simulator, so the
demo works without any hardware. Real ESP32 devices replace this module.
"""
import math
import random
from datetime import date

from config import STEP_MIN


class RoomDay:
    """One room on one calendar day: who is inside at every minute and what sensors read."""

    def __init__(self, room, slots, day: date, seed_extra=0):
        self.room = room
        seed = room["id"] * 1_000_003 + day.toordinal() * 31 + seed_extra
        rng = random.Random(seed)
        self.rng = rng
        self.cloud = rng.uniform(0.4, 1.0)
        self.base_temp = rng.uniform(25.0, 29.0)
        self.base_hum = rng.uniform(50, 70)
        self.sessions = []                      # (start, end, n_people, quiet)
        for s, e, ratio in slots:
            if rng.random() < 0.12:             # class cancelled
                continue
            n = max(1, round(room["capacity"] * ratio * rng.uniform(0.7, 1.0)))
            s2 = s + rng.choice([0, 0, 0, 5])
            e2 = e + rng.choice([0, 0, 0, 5, 10])
            self.sessions.append((s2, e2, n, rng.random() < 0.15))   # 15 % silent exams
        # unscheduled self-study / meetings
        weekend = day.weekday() >= 5
        for h in range(8, 21):
            p = 0.03 if weekend else (0.10 if h < 18 else 0.04)
            start = h * 60 + rng.randint(0, 30)
            if any(s - 5 <= start < e + 5 for s, e, _, _ in self.sessions):
                continue
            if rng.random() < p:
                self.sessions.append((start, start + rng.randint(20, 60), rng.randint(2, 10), True))

    # ---- ground truth ------------------------------------------------------
    def people(self, minute):
        total, quiet = 0, False
        for s, e, n, q in self.sessions:
            if s - 8 <= minute < s + 2:
                k = round(n * (minute - (s - 8)) / 10)
            elif s + 2 <= minute < e:
                k = n
            elif e <= minute < e + 5:
                k = round(n * (1 - (minute - e) / 5))
            else:
                continue
            if k > 0:
                total += k
                quiet = quiet or q
        return total, quiet

    # ---- sensors -----------------------------------------------------------
    def sense(self, minute, state):
        """Return (people, pir, temp, humidity, lux) for the interval ending at `minute`."""
        r = self.rng
        n, quiet = self.people(minute)
        if n > 0:
            p = 0.45 + 0.04 * min(n, 12)
            if quiet:
                p *= 0.45                      # people sitting still -> PIR often silent
        else:
            p = 0.02                           # false triggers (airflow, sun, insects)
        pir = 1 if r.random() < p else 0

        h = minute / 60.0
        outdoor = self.base_temp + 5.5 * math.sin(2 * math.pi * (h - 9) / 24)
        temp = 0.78 * outdoor + 5.2 + 0.025 * n + r.gauss(0, 0.4)
        hum = self.base_hum - 0.9 * (outdoor - 26) + 0.05 * n + r.gauss(0, 2)
        if state.get("ac"):
            temp = max(22.0, temp - 4.5)
            hum -= 8
        daylight = 0.0
        if 6.5 <= h <= 18:
            daylight = 520 * math.sin(math.pi * (h - 6.5) / 11.5) ** 1.2 * self.cloud
        lux = max(0.0, daylight + (300 if state.get("light") else 0) + r.gauss(0, 15))
        return n, pir, round(temp, 1), round(max(20, min(95, hum)), 1), round(lux)


def random_state(rng):
    return {"light": rng.random() < 0.5, "fan": rng.random() < 0.5, "ac": rng.random() < 0.35}
