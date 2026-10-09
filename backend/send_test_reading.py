"""Send one fake ESP32 reading to the backend (handy for testing without hardware).

  python send_test_reading.py --room 1 --pir 1 --temp 29 --humidity 60 --lux 120
Start the server with SCE_SIM=0 if you do not want simulated data mixed in.
"""
import argparse
import json
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:5000/api/telemetry")
ap.add_argument("--key", default="sce-demo-key")
ap.add_argument("--room", type=int, default=1)
ap.add_argument("--pir", type=int, default=1)
ap.add_argument("--temp", type=float, default=28.0)
ap.add_argument("--humidity", type=float, default=60.0)
ap.add_argument("--lux", type=float, default=150.0)
a = ap.parse_args()

body = json.dumps(dict(classroom_id=a.room, pir=a.pir, temp=a.temp, humidity=a.humidity, lux=a.lux)).encode()
req = urllib.request.Request(a.url, body, {"Content-Type": "application/json", "X-API-Key": a.key})
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
