"""
Run:
    pip install -r requirements.txt
    python app.py
Opening http://localhost:5000
"""

import os
import random
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

from model.predictor import WateringPredictor

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("CANOPY_MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("CANOPY_MONGO_DB", "canopy_monitor")
WEATHER_API_KEY = os.environ.get("CANOPY_WEATHER_API_KEY", "")  # OpenWeatherMap key
USE_SIMULATOR = os.environ.get("CANOPY_USE_SIMULATOR", "0") == "1"

THRESHOLDS = {
    "soil_moisture": {"min": 30, "max": 70},   # %
    "temperature": {"min": 18, "max": 29},     # deg C
    "humidity": {"min": 40, "max": 80},        # % (from the DHT22)
}

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Storage layer: try MongoDB, fall back to an in-memory ring buffer so the
# dashboard still works out of the box during a demo without Mongo running.
# ---------------------------------------------------------------------------
class Store:
    def __init__(self):
        self.mongo_ok = False
        self.readings = []   # in-memory fallback
        self.alerts = []
        try:
            from pymongo import MongoClient
            self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=800)
            self.client.server_info()  # force connection check
            self.db = self.client[MONGO_DB_NAME]
            self.mongo_ok = True
            print("[store] connected to MongoDB at", MONGO_URI)
        except Exception as exc:  # noqa: BLE001
            print(f"[store] MongoDB unavailable ({exc}); using in-memory store")

    def insert_reading(self, reading):
        if self.mongo_ok:
            self.db.readings.insert_one(dict(reading))
        else:
            self.readings.append(reading)
            self.readings = self.readings[-2000:]

    def latest(self):
        if self.mongo_ok:
            doc = self.db.readings.find_one(sort=[("ts", -1)])
            return doc
        return self.readings[-1] if self.readings else None

    def history(self, minutes=60):
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        if self.mongo_ok:
            cursor = self.db.readings.find({"ts": {"$gte": cutoff}}).sort("ts", 1)
            return list(cursor)
        return [r for r in self.readings if r["ts"] >= cutoff]

    def insert_alert(self, alert):
        if self.mongo_ok:
            self.db.alerts.insert_one(dict(alert))
        else:
            self.alerts.append(alert)
            self.alerts = self.alerts[-200:]

    def recent_alerts(self, limit=20):
        if self.mongo_ok:
            cursor = self.db.alerts.find().sort("ts", -1).limit(limit)
            return list(cursor)
        return list(reversed(self.alerts[-limit:]))


store = Store()
predictor = WateringPredictor()


def _clean(doc):
    """Strip Mongo's ObjectId and format datetimes for JSON."""
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    if isinstance(doc.get("ts"), datetime):
        doc["ts"] = doc["ts"].isoformat() + "Z"
    return doc


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
def check_thresholds(reading):
    fired = []
    for key, bounds in THRESHOLDS.items():
        value = reading.get(key)
        if value is None:
            continue
        if value < bounds["min"] or value > bounds["max"]:
            severity = "critical" if (value < bounds["min"] * 0.8 or value > bounds["max"] * 1.2) else "warning"
            alert = {
                "ts": reading["ts"],
                "metric": key,
                "value": value,
                "bounds": bounds,
                "severity": severity,
                "message": f"{key.replace('_', ' ').title()} at {value:.1f} is outside safe range "
                           f"({bounds['min']}-{bounds['max']})",
            }
            store.insert_alert(alert)
            fired.append(alert)
    return fired


# ---------------------------------------------------------------------------
# Sensor ingestion (called by real ESP32 hardware, or the simulator below)
# ---------------------------------------------------------------------------
def ingest(soil_moisture, temperature, humidity=None, zone="zone-1"):
    reading = {
        "ts": datetime.utcnow(),
        "zone": zone,
        "soil_moisture": round(float(soil_moisture), 2),
        "temperature": round(float(temperature), 2),
    }
    if humidity is not None:
        reading["humidity"] = round(float(humidity), 2)
    store.insert_reading(reading)
    check_thresholds(reading)
    return reading


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """ESP32 / any real sensor node POSTs JSON here:
    { "soil_moisture": 42.3, "temperature": 24.1, "humidity": 55.0, "zone": "zone-1" }
    humidity is optional; soil_moisture and temperature are required.
    """
    data = request.get_json(force=True, silent=True) or {}
    required = ("soil_moisture", "temperature")
    if not all(k in data for k in required):
        return jsonify({"error": f"missing fields, need {required}"}), 400
    reading = ingest(data["soil_moisture"], data["temperature"], data.get("humidity"),
                      data.get("zone", "zone-1"))
    return jsonify(_clean(reading)), 201


@app.route("/api/latest")
def api_latest():
    return jsonify(_clean(store.latest()))


@app.route("/api/history")
def api_history():
    minutes = int(request.args.get("minutes", 60))
    rows = [_clean(r) for r in store.history(minutes)]
    return jsonify(rows)


@app.route("/api/alerts")
def api_alerts():
    return jsonify([_clean(a) for a in store.recent_alerts()])


@app.route("/api/predict")
def api_predict():
    """Predicts hours until the next watering is needed, and an estimated
    water-savings percentage versus a fixed daily watering schedule."""
    history = store.history(minutes=180)
    if len(history) < 3:
        return jsonify({"hours_until_watering": None, "water_savings_pct": None,
                        "note": "not enough data yet"})
    result = predictor.predict(history)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Watering advice — the main decision the whole system exists to make.
#   moisture < min            -> WATER NOW
#   moisture > max            -> TOO WET, don't water
#   drying out, < 6 h to min  -> WATER SOON
#   otherwise                 -> DON'T WATER
# min/max are THRESHOLDS["soil_moisture"] (percent of your calibrated dry->wet scale).
# ---------------------------------------------------------------------------
WATER_SOON_HOURS = 6


def watering_advice():
    low = THRESHOLDS["soil_moisture"]["min"]
    high = THRESHOLDS["soil_moisture"]["max"]
    recent = store.history(minutes=180)
    if not recent:
        return {"status": "waiting", "title": "Waiting for sensor data",
                "detail": "No readings yet. Check that the ESP32 is powered and sending data.",
                "soil_moisture": None, "hours_until_watering": None}

    last = recent[-5:]  # average the last few readings so one noisy value can't flip the advice
    moisture = sum(r["soil_moisture"] for r in last) / len(last)

    hours = None
    if len(recent) >= 3:
        try:
            hours = predictor.predict(recent)["hours_until_watering"]
        except Exception:  # noqa: BLE001 (e.g. TensorFlow not installed)
            hours = None

    if moisture < low:
        status, title = "water-now", "Water now"
        detail = f"Soil moisture is {moisture:.0f}%, below the {low}% minimum. The plant needs water."
    elif moisture > high:
        status, title = "too-wet", "Too wet — don't water"
        detail = f"Soil moisture is {moisture:.0f}%, above the {high}% maximum. Hold off until it dries below {high}%."
    elif hours is not None and hours < WATER_SOON_HOURS:
        status, title = "water-soon", "Water soon"
        detail = f"Soil moisture is {moisture:.0f}% and drying. Expected to reach {low}% in about {hours:.0f} hours."
    else:
        status, title = "ok", "Don't water yet"
        detail = f"Soil moisture is {moisture:.0f}%, inside the safe {low}–{high}% range."
        if hours is not None and hours < 72:
            detail += f" Next watering expected in about {hours:.0f} hours."

    return {"status": status, "title": title, "detail": detail,
            "soil_moisture": round(moisture, 1), "hours_until_watering": hours}


@app.route("/api/advice")
def api_advice():
    return jsonify(watering_advice())


@app.route("/api/thresholds")
def api_thresholds():
    return jsonify(THRESHOLDS)


# ---------------------------------------------------------------------------
# Built-in simulator — lets the dashboard demo run with zero hardware.
# Disable with CANOPY_USE_SIMULATOR=0 once real ESP32 sensors are POSTing data.
# ---------------------------------------------------------------------------
def _simulator_loop():
    soil, temp, hum = 55.0, 24.0, 55.0
    while True:
        # soil dries out slowly, jumps up on a "watering event"
        soil -= random.uniform(0.3, 1.1)
        if soil < 28:
            soil = random.uniform(60, 68)  # simulated irrigation event
        temp += random.uniform(-0.4, 0.4)
        temp = max(16, min(32, temp))
        hum += random.uniform(-2, 2)
        hum = max(30, min(90, hum))
        ingest(soil, temp, hum)
        time.sleep(5)


if USE_SIMULATOR:
    threading.Thread(target=_simulator_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, use_reloader=False, port=5000)  # 0.0.0.0 lets the ESP32 reach this laptop over WiFi