"""Comprehensive test suite for Smart Classroom Energy system.

Verifies:
1. Empty room shut-off policy (Scenario 1)
2. Occupancy entry and comfort actuation (Scenario 2)
3. Timetable scheduled vs actual occupancy (Scenario 3)
4. Sensor boundary validation and safe fallback (Scenario 4)
5. Manual override control and persistence (Scenario 5)
6. Energy accounting, baseline, cost, and CO2 formulas (Scenario 6)
7. Full REST API endpoints and error responses (Scenario 7)
"""
import json
import os
import sys
import unittest

# Add backend directory to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import config as C
import controller
import db
import service
from app import app, bootstrap


class TestSmartClassroomEnergy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure test in-memory or isolated database
        cls.test_db = os.path.join(BACKEND_DIR, "data", "test_classroom.db")
        os.environ["SCE_DB"] = cls.test_db
        C.DB_PATH = cls.test_db
        db.DB_PATH = cls.test_db
        bootstrap()
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        # Clean up test database files if needed
        for ext in ("", "-shm", "-wal"):
            f = cls.test_db + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    # ------------------------------------------------------------------------
    # Scenario 1: Empty room shut-off after configured grace period
    # ------------------------------------------------------------------------
    def test_scenario_1_empty_room_shutoff(self):
        room = dict(id=1, name="CS-101", ac_w=1500, light_w=240, fan_w=300)
        prev = dict(light=1, fan=1, ac=1)
        # Empty room with no motion for 15 minutes (grace period is 10 min)
        f = dict(temp=28.0, humidity=55.0, lux=200.0, mins_since_motion=15, motion_ratio_15m=0.0, scheduled_now=0)
        prob = 0.10  # 10% chance occupied

        light, fan, ac, occupied, reason = controller.decide(prob, f, prev, room)
        self.assertEqual(light, 0, "Lights must be switched OFF in empty room after grace period")
        self.assertEqual(fan, 0, "Fan must be switched OFF in empty room after grace period")
        self.assertEqual(ac, 0, "AC must be switched OFF in empty room after grace period")
        self.assertEqual(occupied, 0)
        self.assertIn("Empty", reason)

    # ------------------------------------------------------------------------
    # Scenario 2: Students enter room -> appliances actuate based on comfort
    # ------------------------------------------------------------------------
    def test_scenario_2_occupied_room_actuation(self):
        room = dict(id=1, name="CS-101", ac_w=1500, light_w=240, fan_w=300)
        prev = dict(light=0, fan=0, ac=0)
        # Occupied room, hot (29°C), dim ambient light (180 lux < LUX_ON 300)
        f = dict(temp=29.0, humidity=65.0, lux=180.0, mins_since_motion=0, motion_ratio_15m=0.8, scheduled_now=1)
        prob = 0.95  # 95% chance occupied

        light, fan, ac, occupied, reason = controller.decide(prob, f, prev, room)
        self.assertEqual(occupied, 1)
        self.assertEqual(light, 1, "Lights must turn ON when ambient light is dim (180 lux < 300)")
        self.assertEqual(ac, 1, "AC must turn ON when room is hot and occupancy is confident")
        self.assertEqual(fan, 0, "Fan stays OFF while AC is active")
        self.assertIn("Occupied", reason)

    # ------------------------------------------------------------------------
    # Scenario 3: Timetable scheduled class, but room remains quiet
    # ------------------------------------------------------------------------
    def test_scenario_3_scheduled_but_empty_cooling_policy(self):
        room = dict(id=1, name="CS-101", ac_w=1500, light_w=240, fan_w=300)
        prev = dict(light=0, fan=0, ac=0)
        # Class is timetabled, but motion probability is low / cancelled class
        f = dict(temp=26.0, humidity=50.0, lux=500.0, mins_since_motion=25, motion_ratio_15m=0.0, scheduled_now=1)
        prob = 0.20

        light, fan, ac, occupied, reason = controller.decide(prob, f, prev, room)
        self.assertEqual(ac, 0, "AC must NOT switch ON solely from schedule if room is empty")
        self.assertEqual(light, 0)

    # ------------------------------------------------------------------------
    # Scenario 4: Sensor input validation and safe rejection
    # ------------------------------------------------------------------------
    def test_scenario_4_sensor_input_validation(self):
        # Temperature out of physical bounds (999°C)
        invalid_payload = dict(classroom_id=1, pir=1, temp=999.0, humidity=50.0, lux=200.0)
        with self.assertRaises(ValueError):
            service.validate(invalid_payload)

        # Humidity out of physical bounds (150%)
        invalid_humidity = dict(classroom_id=1, pir=1, temp=25.0, humidity=150.0, lux=200.0)
        with self.assertRaises(ValueError):
            service.validate(invalid_humidity)

        # Unknown classroom ID (9999)
        unknown_room = dict(classroom_id=9999, pir=1, temp=25.0, humidity=50.0, lux=200.0)
        with self.assertRaises(ValueError):
            service.validate(unknown_room)

    # ------------------------------------------------------------------------
    # Scenario 5: Manual override mode respects human operator
    # ------------------------------------------------------------------------
    def test_scenario_5_manual_override(self):
        # Override room 1 to manual mode with light=1, fan=1, ac=0
        res = self.client.post("/api/classrooms/1/override",
                               data=json.dumps(dict(mode="manual", light=1, fan=1, ac=0)),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)

        # Check that state table records manual mode
        st = db.one("SELECT mode, light, fan FROM state WHERE classroom_id=1")
        self.assertEqual(st["mode"], "manual")
        self.assertEqual(st["light"], 1)
        self.assertEqual(st["fan"], 1)

        # Switch back to automatic mode
        res_auto = self.client.post("/api/classrooms/1/override",
                                    data=json.dumps(dict(mode="auto")),
                                    content_type="application/json")
        self.assertEqual(res_auto.status_code, 200)
        st_auto = db.one("SELECT mode, reason FROM state WHERE classroom_id=1")
        self.assertEqual(st_auto["mode"], "auto")
        self.assertIn("Automation resumed", st_auto["reason"])

    # ------------------------------------------------------------------------
    # Scenario 6: Energy accounting and baseline KPIs
    # ------------------------------------------------------------------------
    def test_scenario_6_energy_accounting(self):
        rows = [
            dict(energy_kwh=10.0, baseline_kwh=30.0),
            dict(energy_kwh=5.0, baseline_kwh=15.0),
        ]
        from app import kpis
        metrics = kpis(rows)
        self.assertEqual(metrics["energy_kwh"], 15.0)
        self.assertEqual(metrics["baseline_kwh"], 45.0)
        self.assertEqual(metrics["saved_kwh"], 30.0)
        self.assertEqual(metrics["saved_pct"], 66.7)
        self.assertEqual(metrics["cost_saved_inr"], round(30.0 * C.TARIFF_INR_PER_KWH))
        self.assertEqual(metrics["co2_saved_kg"], round(30.0 * C.CO2_KG_PER_KWH, 1))

    # ------------------------------------------------------------------------
    # Scenario 7: API endpoints integrity and error handling
    # ------------------------------------------------------------------------
    def test_scenario_7_api_endpoints(self):
        # /api/health
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

        # /api/overview
        res_ov = self.client.get("/api/overview")
        self.assertEqual(res_ov.status_code, 200)
        data = res_ov.get_json()
        self.assertIn("totals", data)
        self.assertIn("rooms", data)
        self.assertEqual(len(data["rooms"]), 6)

        # /api/energy/hourly with fallback
        res_hr = self.client.get("/api/energy/hourly?date=invalid-date")
        self.assertEqual(res_hr.status_code, 200)

        # /api/energy/daily
        res_day = self.client.get("/api/energy/daily?days=7")
        self.assertEqual(res_day.status_code, 200)

        # 404 for unknown classroom
        res_404 = self.client.get("/api/classrooms/999/history")
        self.assertEqual(res_404.status_code, 404)


if __name__ == "__main__":
    unittest.main()
