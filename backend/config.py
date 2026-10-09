"""Central configuration for Smart Classroom Energy.

Manages application paths, controller comfort thresholds, ML model artifacts,
energy tariffs, security parameters, and background simulation settings.
"""
from typing import Tuple
import os

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR: str = os.path.join(os.path.dirname(BASE_DIR), "frontend")

DB_PATH: str = os.environ.get("SCE_DB", os.path.join(BASE_DIR, "data", "smart_classroom.db"))
MODEL_PATH: str = os.path.join(BASE_DIR, "ml", "model.joblib")
METRICS_PATH: str = os.path.join(BASE_DIR, "ml", "metrics.json")

# Shared secret that ESP32 devices send in the X-API-Key header.
API_KEY: str = os.environ.get("SCE_API_KEY", "sce-demo-key")

# Security & Rate Limiting (DoS prevention)
MAX_REQUEST_BODY_BYTES: int = 1 * 1024 * 1024  # 1MB
RATE_LIMIT_ENABLED: bool = os.environ.get("SCE_RATE_LIMIT_ENABLED", "1") != "0"
RATE_LIMIT_REQUESTS_PER_MIN: int = int(os.environ.get("SCE_RATE_LIMIT", "180"))

# --- Sampling -------------------------------------------------------------
STEP_MIN: int = 5                      # minutes between sensor reports

# --- Controller thresholds -------------------------------------------------
P_OCCUPIED: float = 0.55               # probability above which room is "occupied"
P_VACANT: float = 0.30                 # probability below which room may be "vacant"
GRACE_MIN: int = 10                    # no motion for this long before switching OFF
P_AC_ALLOWED: float = 0.75             # AC only when occupancy is confident
LUX_ON: int = 300                      # lights ON below this ambient light
LUX_OFF: int = 650                     # lights OFF above this (hysteresis: lamp adds ~300 lux)
FAN_ON_TEMP: float = 25.0
FAN_HOLD_TEMP: float = 24.0
FAN_ON_HUMIDITY: float = 75.0
AC_ON_TEMP: float = 27.5
AC_HOLD_TEMP: float = 23.0

# --- Energy accounting -----------------------------------------------------
TARIFF_INR_PER_KWH: float = 8.0        # institutional tariff, change to your DISCOM rate
CO2_KG_PER_KWH: float = 0.82           # approximate Indian grid emission factor
OPEN_FROM_H: int = 8
OPEN_TO_H: int = 17                    # baseline: appliances left ON in these hours
OPEN_DAYS: Tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday-Friday

# --- Demo simulator --------------------------------------------------------
SIM_ENABLED: bool = os.environ.get("SCE_SIM", "1") != "0"
SIM_TICK_SEC: float = float(os.environ.get("SCE_SIM_TICK", "2"))   # real seconds per 5 simulated minutes
SIM_BACKFILL_DAYS: int = 4
