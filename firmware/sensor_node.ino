

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>

const char* WIFI_SSID     = "********";
const char* WIFI_PASSWORD = "*********";
const char* SERVER_URL    = "http://192.168.1.40:5000/api/ingest";  

const int SOIL_DRY_RAW = 2084;
const int SOIL_WET_RAW = 1191;
// ---------------------------------------------------

#define SOIL_PIN 34
#define DHT_PIN  4
#define DHT_TYPE DHT22

DHT dht(DHT_PIN, DHT_TYPE);

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("Connected! ESP32 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("WiFi failed. Check name/password and that it is a 2.4 GHz network.");
  }
}

int readSoilRaw() {
  long total = 0;
  for (int i = 0; i < 10; i++) {
    total += analogRead(SOIL_PIN);
    delay(10);
  }
  return (int)(total / 10);
}

float soilPercent(int raw) {
  float pct = (float)(SOIL_DRY_RAW - raw) * 100.0 / (float)(SOIL_DRY_RAW - SOIL_WET_RAW);
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  dht.begin();
  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  int raw = readSoilRaw();
  float soil = soilPercent(raw);
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  Serial.print("soil raw: ");
  Serial.print(raw);
  Serial.print(" | soil %: ");
  Serial.print(soil, 1);
  Serial.print(" | temp C: ");
  Serial.print(temp, 1);
  Serial.print(" | humidity %: ");
  Serial.println(hum, 1);

  if (isnan(temp) || isnan(hum)) {
    Serial.println("DHT22 read failed - check wiring and the 10k resistor. Skipping this cycle.");
    delay(5000);
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.setTimeout(4000);
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");

    String payload = "{\"soil_moisture\":" + String(soil, 1) +
                     ",\"temperature\":" + String(temp, 1) +
                     ",\"humidity\":" + String(hum, 1) +
                     ",\"zone\":\"zone-1\"}";

    int code = http.POST(payload);
    if (code > 0) {
      Serial.print("POST result: ");
      Serial.println(code);
    } else {
      Serial.print("POST failed: ");
      Serial.println(http.errorToString(code));
    }
    http.end();
  }

  delay(5000);
}
