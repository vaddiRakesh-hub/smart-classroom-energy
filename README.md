# Smart Classroom Energy

**AI prediction + timetable + real-time sensing + IoT automation + energy analytics**

> Smart Classroom Energy uses AI to predict classroom usage and IoT to automatically use electricity only when it is actually needed.

---

## 1. Problem

In most colleges, lights, fans and air conditioners stay on in classrooms that are empty: between lectures, after labs, during cancelled classes, and over lunch. Nobody is responsible for switching them off, so electricity is wasted and campus energy bills grow.

## 2. Solution

Each classroom gets a low-cost **ESP32 node** with a PIR motion sensor, a temperature/humidity sensor, a light sensor and relays. The node reports to a Python backend every 5 minutes. The backend combines **three sources of information**:

| Source | What it tells us |
|---|---|
| **Timetable** | Is a class scheduled? How many students are expected? When does the next class start? |
| **Historical usage** | Patterns learned by the Random Forest (late-running classes, study sessions, cancelled lectures) |
| **Real-time sensors** | Motion in the last 5/15 minutes, minutes since last motion, light level, temperature, humidity |

A **Random Forest** turns these into one number: *the probability that the room will be occupied in the next interval*. A **controller** converts that probability into relay commands, and a **web dashboard** shows occupancy, appliance status, energy use and savings.

### Why it is more than a motion-activated switch

A motion switch has two failure modes: it **switches off on people sitting still** (exams, lectures) and it **switches on for false triggers** (airflow, insects). It also cannot **prepare a room** before a class. Our system:

* keeps appliances on during a timetabled class even if the PIR is quiet for a while (exam, silent reading),
* does not switch anything on for a stray motion event in an empty, unscheduled room,
* uses a grace period and hysteresis, so relays never flicker,
* only uses the air conditioner when occupancy is confident and the room is actually hot,
* picks *which* appliances are needed (lights only when it is dim, fan or AC by temperature).

Measured on held-out simulated days (see section 7): Random Forest **F1 = 0.89** vs **0.86** timetable-only and **0.76** motion-only.

---

## 3. System architecture

```
 CLASSROOM (one per room)                          SERVER (PC / Raspberry Pi / cloud VM)
┌───────────────────────────┐   HTTP POST JSON    ┌──────────────────────────────────────────┐
│ ESP32                     │ ─────────────────►  │ Flask API  /api/telemetry                │
│  PIR  ─────────┐          │                     │   1. validate + store reading            │
│  DHT22 ────────┤ sensors  │                     │   2. build features (timetable+history)  │
│  BH1750 ───────┘          │  ◄───────────────── │   3. Random Forest -> P(occupied)        │
│                           │   {light,fan,ac}    │   4. Controller rules -> commands        │
│  Relays ► light/fan/AC    │                     │   5. log energy, baseline and events     │
└───────────────────────────┘                     │ SQLite database                          │
                                                  │ Web dashboard (HTML/CSS/JS) on same port │
        Admin browser  ◄────── /api/overview ─────┴──────────────────────────────────────────┘
```

**Loop timing:** sense -> report (5 min) -> predict -> command. The label the model predicts is *"will the room be occupied in the next 5-minute interval?"*, so decisions look ahead by one interval instead of reacting late.

---

## 4. Project structure

```
smart-classroom-energy/
├── README.md                    <- this file
├── requirements.txt
├── run.sh / run.bat             <- one-command start (Linux/macOS / Windows)
├── backend/
│   ├── app.py                   Flask app: device API + dashboard API + serves frontend
│   ├── config.py                thresholds, tariff, power ratings, simulator settings
│   ├── db.py                    SQLite schema and helpers
│   ├── timetable.py             classroom master data + demo timetable generator
│   ├── service.py               pipeline: telemetry -> features -> ML -> controller -> DB
│   ├── controller.py            rule layer (hysteresis, grace period, comfort rules)
│   ├── simulate.py              physical world model (people, PIR, temp, light)
│   ├── simulator.py             virtual ESP32 nodes for the demo (no hardware needed)
│   ├── send_test_reading.py     send one fake ESP32 reading with curl-like script
│   ├── ml/
│   │   ├── features.py          feature engineering (shared by training and live)
│   │   ├── train_model.py       dataset generation + Random Forest training + evaluation
│   │   ├── predictor.py         model loading and inference
│   │   ├── model.joblib         trained model (re-created by train_model.py)
│   │   └── metrics.json         evaluation results shown on the dashboard
│   └── data/                    SQLite database is created here on first run
├── frontend/
│   ├── index.html               dashboard page
│   ├── style.css
│   └── app.js                   vanilla JS, SVG charts, no build step
└── firmware/esp32_smart_classroom/esp32_smart_classroom.ino
```

---

## 5. Quick start (no hardware needed)

Requirements: **Python 3.9+**.

```bash
cd smart-classroom-energy
./run.sh                 # Windows: double-click run.bat
# or manually:
pip install -r requirements.txt
cd backend && python app.py
```

Open **http://localhost:5000**.

On first start the demo simulator builds 4 days of history (about 10 seconds) and then runs a live virtual campus: **5 simulated minutes pass every 2 seconds**, so a full day takes about 10 minutes. Click any classroom tile to see sensor values, why the system made its decision, the day's occupancy chart, the timetable, and to take manual control.

| Setting (environment variable) | Default | Meaning |
|---|---|---|
| `PORT` | 5000 | Web server port |
| `SCE_SIM` | 1 | `0` disables the simulator (use when real ESP32 nodes are connected) |
| `SCE_SIM_TICK` | 2 | Real seconds per simulated 5 minutes |
| `SCE_API_KEY` | sce-demo-key | Key the ESP32 must send in `X-API-Key` |
| `SCE_DB` | backend/data/smart_classroom.db | Database path (delete the file to reset the demo) |

**Retrain the model** (about 30 seconds): `cd backend && python -m ml.train_model`

---

## 6. How it works in detail

### 6.1 Hardware (per classroom)

| Part | Purpose | ESP32 pin |
|---|---|---|
| ESP32 DevKit | Wi-Fi + logic | |
| PIR sensor (HC-SR501 or AM312) | Motion | GPIO 27 |
| DHT22 | Temperature and humidity | GPIO 4 (10 kΩ pull-up to 3.3 V) |
| BH1750 | Ambient light in lux (I2C) | SDA GPIO 21, SCL GPIO 22 |
| 3-channel 5 V relay module | Lights, fans, AC | GPIO 25, 26, 33 |
| 5 V power supply | Powers ESP32 and relay board | |

> **Safety:** relays switch mains voltage. Mains wiring must be done by a qualified electrician, inside an enclosure, with proper fusing. For an air conditioner, drive a contactor or use an IR blaster instead of switching the compressor directly. A prototype can be demonstrated with LEDs/bulbs on a low-voltage board.

The firmware (`firmware/esp32_smart_classroom/`) latches PIR events with an interrupt so brief motion between reports is not missed, posts a JSON report, and applies the returned commands. If the server is unreachable it **fails safe**: all appliances switch OFF after 15 minutes without motion.

### 6.2 Telemetry API (device -> server)

```http
POST /api/telemetry
X-API-Key: sce-demo-key
Content-Type: application/json

{"classroom_id": 1, "pir": 1, "temp": 28.4, "humidity": 61, "lux": 180, "interval_min": 5}
```

Response (applied to the relays by the ESP32):

```json
{"classroom_id": 1, "light": 1, "fan": 1, "ac": 0, "mode": "auto",
 "occupied": 1, "probability": 0.97,
 "reason": "Occupied (97%, class in session) - lights ON (dim room), fan ON", "interval_s": 300}
```

`ts` (ISO time) is optional; the server uses its own clock if absent.

### 6.3 Machine learning

* **Model:** `RandomForestClassifier` (100 trees, depth 12, class-balanced). It handles mixed numeric features, needs no scaling, is robust to sensor noise, runs in milliseconds on a Raspberry Pi and provides feature importances that are easy to explain.
* **Target:** room occupied in the next 5-minute interval (yes/no).
* **Features (12):**

| Group | Features |
|---|---|
| Time | `hour_frac`, `dow` |
| Timetable | `scheduled_now`, `mins_to_next_class`, `mins_since_class_end`, `expected_ratio` (expected students / capacity) |
| Motion | `pir`, `motion_ratio_15m`, `mins_since_motion` |
| Environment | `lux`, `temp`, `humidity` |

* **Training data:** `simulate.py` models each room-day: scheduled classes (12 % cancelled, some run late), unscheduled self-study sessions, silent exams, students arriving and leaving gradually, a PIR that misses still people (and triggers falsely 2 % of the time), daylight-dependent lux, and temperature that follows the outdoor cycle, occupants and AC state. 100 days x 6 rooms = about 115,000 samples, split **chronologically** (first 80 days train, last 20 days test) so there is no leakage.
* **Using real data instead:** log real readings for a few weeks (the `readings` table already stores everything), label each row with the next interval's occupancy (from a people counter, door counter or manual audit), put it in the same feature layout and call `train()`.

### 6.4 Controller (`controller.py`)

| Situation | Action |
|---|---|
| P(occupied) <= 0.30 **and** no motion for >= 10 min | All appliances OFF |
| 0.30 < P < 0.55 | Hold current state (never cuts power in a room that may be in use) |
| P >= 0.55 | Occupied: lights ON if lux < 300 (OFF again above 650, so the lamp's own light does not cause flicker) |
| Occupied and P >= 0.75 and class in session (or steady motion) and room has AC and temp >= 27.5 °C | AC ON (held until temp < 23 °C) |
| Occupied, no AC, and temp >= 25 °C or humidity >= 75 % | Fan ON (held until temp < 24 °C); fan is OFF while the AC runs |
| Manual override | Automation paused for that room until set back to Automatic |

All thresholds live in `config.py`.

### 6.5 Energy and savings calculation

* Each report stores the room's actual power: `light_w + fan_w + ac_w` for whatever is ON, and the interval length.
* **Baseline** = the room with every appliance ON from 08:00 to 17:00 on weekdays (the behaviour the project is fixing). Outside those hours no saving is claimed.
* `kWh = power x interval / 60 / 1000`; **saved = baseline - actual**; cost = kWh x tariff (Rs 8/kWh, editable); CO2 = kWh x 0.82 kg (approximate Indian grid factor).
* The monthly projection on the dashboard is the average saving of complete working days x 22 working days.

### 6.6 Database (SQLite)

| Table | Purpose |
|---|---|
| `classrooms` | Room name, type, building, capacity, appliance wattages |
| `timetable` | Weekly classes (day, start, end, subject, expected students) |
| `readings` | Every sensor report with probability, appliance states, power and baseline |
| `state` | Latest state per room (also holds Auto/Manual mode and the decision reason) |
| `events` | Log of every switch action for the "Recent actions" feed |

### 6.7 Dashboard API

| Endpoint | Description |
|---|---|
| `GET /api/overview` | Totals, every room's live state, recent actions |
| `GET /api/energy/hourly?date=YYYY-MM-DD` | Baseline vs actual kWh per hour |
| `GET /api/energy/daily?days=7` | Daily totals and monthly projection |
| `GET /api/classrooms/<id>/history` | Day's readings and timetable for one room |
| `POST /api/classrooms/<id>/override` | `{"mode":"auto"}` or `{"mode":"manual","light":1,"fan":0,"ac":0}` |
| `GET/POST /api/timetable` | Read or add timetable entries (POST: `classroom_id, dow, start_min, end_min, subject, students`) |
| `GET /api/model` | Model metrics and feature importances |
| `POST /api/telemetry` | Device endpoint (needs `X-API-Key`) |
| `GET /api/health` | Health check |

---

## 7. Results (simulated campus)

**Model quality** on 20 unseen days (23,040 test intervals):

| Method | Accuracy | Precision | Recall | F1 |
|---|---|---|---|---|
| **Random Forest (AI + timetable + sensors)** | **95.3 %** | 87.0 % | 90.5 % | **88.7 %** |
| Timetable only | 94.3 % | 89.4 % | 82.1 % | 85.6 % |
| Motion sensor only | 88.7 % | 66.9 % | 88.8 % | 76.3 % |

ROC-AUC of the Random Forest: 0.980. Most important features: `expected_ratio`, `scheduled_now`, `mins_since_motion`, `motion_ratio_15m`.

**Energy:** in the six-room demo campus the system saved roughly **55-60 % of the energy** compared with leaving all appliances on during working hours, about **58 kWh per working day, around Rs 10,000 per month** for six rooms at Rs 8/kWh. These are **simulation results**: the real figure depends on how well the college already switches things off. Measure your own baseline for a week before claiming a number.

---

## 8. Using real ESP32 hardware

1. Start the server with `SCE_SIM=0 python app.py` (this removes simulated nodes).
2. Edit your rooms in `backend/timetable.py` (`ROOMS`, wattages) and delete `backend/data/smart_classroom.db` so the new rooms and timetable are created. Use `POST /api/timetable` or edit the table for the real timetable.
3. Open `firmware/.../esp32_smart_classroom.ino` in Arduino IDE, install the libraries listed in its header, set Wi-Fi, `SERVER_URL` (your PC's IP) and `CLASSROOM_ID`, then upload.
4. Watch the Serial Monitor at 115200 baud: you should see `P(occupied)=... reason` lines and relay changes.
5. No hardware yet? `python backend/send_test_reading.py --room 1 --pir 1 --temp 29 --lux 120` sends a fake report.

Tip: set `REPORT_INTERVAL` to 15 seconds during demos so changes are visible quickly.

---

## 9. Limitations and future work

* The ML model is trained on simulated data; retrain with real logs for production accuracy.
* PIR cannot count people. A door-counter, CO2 sensor or camera-free thermal array would improve occupancy estimates.
* The dashboard has no login. Add authentication (and HTTPS) before exposing it beyond the campus network.
* Per-appliance power is estimated from nameplate ratings. Add current sensors (e.g. PZEM-004T or ACS712) for measured energy.
* Future: MQTT transport, per-building analytics, pre-cooling before classes, holiday calendar integration, LMS/ERP timetable import, mobile alerts, and reinforcement learning for AC set-points.

---

## 10. Technology summary

ESP32, PIR, DHT22 (temperature/humidity), BH1750 (light), relays, Python 3, Flask, SQLite, scikit-learn (Random Forest), pandas/NumPy, HTML/CSS/JavaScript (SVG charts), REST/JSON over Wi-Fi.
