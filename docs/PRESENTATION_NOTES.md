# Presentation notes

**One-line pitch:** Smart Classroom Energy uses AI to predict classroom usage and IoT to automatically use electricity only when it is actually needed.

## 2-minute demo script
1. Open the dashboard. Point at the headline: "Using X kW right now instead of Y kW" - the bar shows each room's real draw against its full load.
2. Click an occupied room (dark tile). Show *why* the system decided what it did (class in session, dim room, hot room).
3. Click an empty room: show its occupancy chance dropping after class and appliances going OFF.
4. In the drawer, switch to Manual, toggle a light, then back to Automatic - shows admin control.
5. Show "Recent actions", the hourly chart (gap = saved energy), and the model comparison (Random Forest vs motion-only vs timetable-only).
6. (Optional) run `python backend/send_test_reading.py --pir 0 --lux 600` and show the JSON command returned to the ESP32.

## Likely questions
* **Why not just a motion sensor?** It switches off on still people and on during false triggers. Our model uses the timetable and motion history (F1 0.89 vs 0.76).
* **Why Random Forest?** Fast, works on small tabular data, robust to noise, explainable through feature importance, runs on cheap hardware.
* **What if Wi-Fi/server fails?** The ESP32 falls back to a 15-minute motion timeout.
* **What if the class is silent (exam)?** The timetable feature keeps the room "occupied" even with no motion.
* **Where does training data come from?** A physical simulation for the prototype; real deployments log readings and retrain.
* **How are savings computed?** Baseline (all appliances on 08:00-17:00 weekdays) minus actual measured-by-schedule power, x tariff.
* **Privacy?** No cameras or microphones; only a motion bit, temperature, humidity and light level.
