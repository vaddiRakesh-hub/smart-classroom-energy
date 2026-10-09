"""Demo simulator: virtual ESP32 nodes so the whole system runs without hardware."""
import random
import threading
import time
from datetime import datetime, timedelta

import config as C
import db
import service
from simulate import RoomDay
from timetable import slots_for


class Simulator:
    def __init__(self):
        self.rng = random.Random(2026)
        self.worlds = {}
        self.cmds = {rid: {"light": 0, "fan": 0, "ac": 0} for rid in service.rooms()}
        self.clock = None            # simulated datetime of the NEXT report
        self.running = False

    def _world(self, rid, day):
        key = (rid, day)
        if key not in self.worlds:
            room = service.rooms()[rid]
            slots = slots_for(service._timetable, rid, day.weekday(), room["capacity"]) if day.weekday() < 5 else []
            self.worlds[key] = RoomDay(room, slots, day)
            for k in [k for k in self.worlds if k[1] < day]:
                del self.worlds[k]
        return self.worlds[key]

    def step(self):
        ts = self.clock
        minute = ts.hour * 60 + ts.minute
        batch = []
        for rid in service.rooms():
            _, pir, temp, hum, lux = self._world(rid, ts.date()).sense(minute, self.cmds[rid])
            batch.append(dict(classroom_id=rid, pir=pir, temp=temp, humidity=hum, lux=lux,
                              ts=ts.isoformat(), interval_min=C.STEP_MIN))
        for c in service.process_batch(batch):
            self.cmds[c["classroom_id"]] = c
        self.clock = ts + timedelta(minutes=C.STEP_MIN)

    def start(self):
        last = db.one("SELECT MAX(ts) AS t FROM readings")["t"]
        if last:                                      # resume after restart
            self.clock = datetime.fromisoformat(last) + timedelta(minutes=C.STEP_MIN)
            for c in db.q("SELECT classroom_id,light,fan,ac FROM state"):
                self.cmds[c["classroom_id"]] = c
        else:                                         # first run: build history, stop at 10:55 today
            today = datetime.now().date()
            while today.weekday() >= 5:
                today -= timedelta(days=1)
            self.clock = datetime.combine(today - timedelta(days=C.SIM_BACKFILL_DAYS), datetime.min.time())
            stop = datetime.combine(today, datetime.min.time()) + timedelta(hours=11)
            print(f"[sim] Back-filling {C.SIM_BACKFILL_DAYS} days of history ...")
            with db.transaction():
                while self.clock < stop:
                    self.step()
            print("[sim] History ready.")
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.running:
            time.sleep(C.SIM_TICK_SEC)
            try:
                self.step()
            except Exception as exc:                  # keep the demo alive
                print("[sim] error:", exc)
