"""Central configuration for Smart Classroom Energy."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")

DB_PATH = os.environ.get("SCE_DB", os.path.join(BASE_DIR, "data", "smart_classroom.db"))
MODEL_PATH = os.path.join(BASE_DIR, "ml", "model.joblib")
METRICS_PATH = os.path.join(BASE_DIR, "ml", "metrics.json")

# Shared secret that ESP32 devices send in the X-API-Key header.
API_KEY = os.environ.get("SCE_API_KEY", "sce-demo-key")

# --- Sampling -------------------------------------------------------------
STEP_MIN = 5                      # minutes between sensor reports

# --- Controller thresholds -------------------------------------------------
P_OCCUPIED = 0.55                 # probability above which room is "occupied"
P_VACANT = 0.30                   # probability below which room may be "vacant"
GRACE_MIN = 10                    # no motion for this long before switching OFF
P_AC_ALLOWED = 0.75               # AC only when occupancy is confident
LUX_ON = 300                      # lights ON below this ambient light
LUX_OFF = 650                     # lights OFF above this (hysteresis: lamp adds ~300 lux)
FAN_ON_TEMP = 25.0
FAN_HOLD_TEMP = 24.0
FAN_ON_HUMIDITY = 75.0
AC_ON_TEMP = 27.5
AC_HOLD_TEMP = 23.0

# --- Energy accounting -----------------------------------------------------
TARIFF_INR_PER_KWH = 8.0          # institutional tariff, change to your DISCOM rate
CO2_KG_PER_KWH = 0.82             # approximate Indian grid emission factor
OPEN_FROM_H, OPEN_TO_H = 8, 17    # baseline: appliances left ON in these hours
OPEN_DAYS = (0, 1, 2, 3, 4)       # Monday-Friday

# --- Demo simulator --------------------------------------------------------
SIM_ENABLED = os.environ.get("SCE_SIM", "1") != "0"
SIM_TICK_SEC = float(os.environ.get("SCE_SIM_TICK", "2"))   # real seconds per 5 simulated minutes
SIM_BACKFILL_DAYS = 4
