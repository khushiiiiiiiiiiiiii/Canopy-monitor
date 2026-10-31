# Canopy — Real-time IVF Monitoring System

A working prototype for the capstone brief: *Real-time Internal Vertical Farm Monitoring System for
Optimized Plant Growth and Reduced Water Consumption*.

**Stack (as required by the brief):** Python Flask · TensorFlow 2.x · MongoDB · D3.js

## What's included

- **Flask backend** (`app.py`) — REST API for sensor ingestion, history, alerts, and predictions
- **MongoDB storage**, with an automatic in-memory fallback so the app still runs
  if Mongo isn't installed yet (handy while developing)
- **TensorFlow model** (`model/predictor.py`) — learns each zone's soil-drying rate
  and predicts hours until the next watering is actually needed, instead of
  watering on a fixed timer. Trains itself on synthetic data in a few seconds
  on first run; swap in real historical readings once you've collected enough
- **D3.js dashboard** (`templates/index.html`, `static/js/dashboard.js`) — live
  readings, 60-minute trend charts, and an alert feed
- **Built-in simulator** — generates realistic sensor data automatically so the
  dashboard has something to show before your ESP32 hardware is wired up

## Quick start (no hardware needed yet)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**. The built-in simulator starts automatically and
the dashboard updates every 5 seconds.

MongoDB is optional for local development — if it isn't running, the app logs
`MongoDB unavailable; using in-memory store` and works exactly the same way
(you just lose persistence across restarts). To use real MongoDB:

```bash
export IVF_MONGO_URI="mongodb://localhost:27017"   # or your Atlas connection string
```

## Connecting real ESP32 hardware

1. Wire up a capacitive soil moisture sensor and a DHT22 (temperature + humidity)
   to your ESP32.
2. Have the ESP32 POST readings every few seconds:

   ```
   POST /api/ingest
   Content-Type: application/json

   { "soil_moisture": 42.3, "temperature": 24.1, "humidity": 55.0, "zone": "zone-1" }
   ```

3. Turn off the simulator so it doesn't mix with real readings:

   ```bash
   export IVF_USE_SIMULATOR=0
   python app.py
   ```

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/ingest` | POST | Push a new sensor reading |
| `/api/latest` | GET | Most recent reading |
| `/api/history?minutes=60` | GET | Reading history for the trend charts |
| `/api/alerts` | GET | Recent threshold-breach alerts |
| `/api/predict` | GET | Hours until next watering + estimated water savings |
| `/api/thresholds` | GET | Configured safe ranges per metric |

Safe ranges (edit in `app.py` → `THRESHOLDS`):

| Metric | Min | Max |
|---|---|---|
| Soil moisture | 30% | 70% |
| Temperature | 18°C | 29°C |
| Humidity | 40% | 80% |

## Watering advice

The dashboard's top card gives the decision the project exists to make, using the soil
moisture range in `THRESHOLDS` (`app.py`) as a percent of your calibrated dry-to-wet scale:

| Soil moisture | Advice |
|---|---|
| below 30% | Water now |
| 30-70% but drying, under 6 h to the minimum | Water soon |
| 30-70% | Don't water yet |
| above 70% | Too wet, don't water |

Tune the 30 and 70 to your plant (watch when it actually looks like it needs water).

## Weather API integration (optional, per the brief)

The brief calls for a weather API to inform watering schedules. To wire one in:
get a free key from [OpenWeatherMap](https://openweathermap.org/api), set
`IVF_WEATHER_API_KEY`, and extend `api_predict()` in `app.py` to factor
forecasted humidity/temperature into the prediction — the model and endpoint
are already structured to make this a small addition rather than a rearchitect.

## Project structure

```
ivf-monitor/
├── app.py                  # Flask app, routes, alerting, simulator
├── model/
│   └── predictor.py        # TensorFlow watering-prediction model
├── templates/
│   └── index.html          # Dashboard page
├── static/
│   ├── css/style.css       # Dashboard styling
│   └── js/dashboard.js     # D3.js charts + polling
├── requirements.txt
└── README.md
```

## Suggested next steps for your team

- Swap the synthetic training data in `predictor.py` for real historical
  readings once you've collected a few days of data from the simulator or hardware
- Add a `/api/zones` concept if you're monitoring more than one grow bed
- Add user auth if this will be deployed somewhere other than a local demo
- Log predicted vs. actual watering outcomes to evaluate the model over time
