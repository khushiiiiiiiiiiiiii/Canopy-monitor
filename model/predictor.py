"""
WateringPredictor
------------------
A small TensorFlow (Keras) regression model that predicts how many hours remain
until the soil dries to the watering threshold.

Inputs (3):
    current soil moisture   how much water is left above the threshold
    drying rate             % moisture lost per hour, measured from recent readings
    temperature             warmer air dries soil faster

Physical idea the model learns:
    hours = (moisture - threshold) / (drying_rate * temperature_factor)

The model trains itself on synthetic data generated from that idea the first time
it is used (a few seconds on CPU). Swap `_synthetic_training_data` for real
historical readings from MongoDB once enough have been collected.
"""

import threading

import numpy as np

FLOOR = 30.0  # soil-moisture % at which the plant needs water


class WateringPredictor:
    def __init__(self):
        self._model = None
        self._trained = False
        self._lock = threading.Lock()

    def _build(self):
        import tensorflow as tf

        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(3,)),
            tf.keras.layers.Dense(16, activation="relu"),
            tf.keras.layers.Dense(16, activation="relu"),
            tf.keras.layers.Dense(1),
        ])
        model.compile(optimizer="adam", loss="mse")
        return model

    @staticmethod
    def _features(moisture, rate, temp):
        """Log-scale the two quantities that get divided, so the network learns a
        near-linear relationship instead of a 1/x curve."""
        above = np.maximum(np.asarray(moisture, dtype="float64") - FLOOR, 0.5)
        rate = np.maximum(np.asarray(rate, dtype="float64"), 0.05)
        temp = np.asarray(temp, dtype="float64")
        return np.stack([np.log(above), np.log(rate), (temp - 22.0) / 10.0], axis=1).astype("float32")

    def _synthetic_training_data(self, n=6000):
        rng = np.random.default_rng(42)
        moisture = rng.uniform(FLOOR + 0.5, 85, n)
        rate = np.exp(rng.uniform(np.log(0.05), np.log(5.0), n))   # % per hour, log-uniform
        temp = rng.uniform(14, 34, n)

        # Higher temperature modestly speeds up drying.
        effective_rate = np.clip(rate * (1 + (temp - 22) * 0.02), 0.05, None)
        hours = np.clip((moisture - FLOOR) / effective_rate, 0, 72)

        X = self._features(moisture, rate, temp)
        y = np.log1p(hours).astype("float32")
        return X, y

    def _ensure_trained(self):
        with self._lock:
            if self._trained:
                return
            self._model = self._build()
            X, y = self._synthetic_training_data()
            self._model.fit(X, y, epochs=40, batch_size=128, verbose=0)
            self._trained = True

    @staticmethod
    def _drying_rate(history):
        """% moisture lost per hour, from the readings in `history`."""
        if len(history) < 2:
            return 0.5
        first, last = history[0], history[-1]
        dt_hours = max((last["ts"] - first["ts"]).total_seconds() / 3600.0, 1 / 60)
        delta = first["soil_moisture"] - last["soil_moisture"]
        rate = delta / dt_hours
        return float(np.clip(rate, 0.05, 5.0))

    def predict(self, history):
        rate = self._drying_rate(history)
        temp = float(np.mean([r["temperature"] for r in history[-10:]]))
        current_moisture = float(np.mean([r["soil_moisture"] for r in history[-5:]]))

        if current_moisture <= FLOOR:
            hours = 0.0
        else:
            self._ensure_trained()
            x = self._features([current_moisture], [rate], [temp])
            hours = float(np.expm1(self._model.predict(x, verbose=0)[0][0]))
            hours = max(0.0, min(72.0, hours))

        # Rough comparison against a fixed daily-watering baseline (24 h cycle).
        # This is a heuristic estimate, not a measurement.
        baseline_hours = 24.0
        if hours <= 0:
            savings_pct = 0.0
        else:
            savings_pct = max(0.0, min(60.0, (hours - baseline_hours / 2) / baseline_hours * 100 + 20))

        return {
            "hours_until_watering": round(hours, 1),
            "current_moisture": round(current_moisture, 1),
            "drying_rate_pct_per_hour": round(rate, 2),
            "water_savings_pct": round(savings_pct, 1),
        }
