/*
 * Smart Classroom Energy - ESP32 node firmware
 *
 * One ESP32 per classroom. Every REPORT_INTERVAL it:
 *   1. reads PIR (latched), temperature/humidity (DHT22) and light (BH1750),
 *   2. POSTs them as JSON to the backend,
 *   3. applies the light/fan/ac commands returned by the AI backend to the relays.
 * If the server cannot be reached it falls back to a simple local rule
 * (appliances OFF after FALLBACK_TIMEOUT with no motion) so power is never wasted.
 *
 * Libraries (Arduino Library Manager): DHT sensor library (Adafruit), Adafruit Unified Sensor,
 *                                       BH1750 (Christopher Laws), ArduinoJson (v7)
 * Board: "ESP32 Dev Module"
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <DHT.h>
#include <BH1750.h>
#include <ArduinoJson.h>

// ---------- EDIT THESE ----------
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASS     = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL    = "http://192.168.1.50:5000/api/telemetry";  // PC running backend/app.py
const char* API_KEY       = "sce-demo-key";                            // must match SCE_API_KEY
const int   CLASSROOM_ID  = 1;                                         // row id in the classrooms table
const unsigned long REPORT_INTERVAL = 5UL * 60UL * 1000UL;             // 5 minutes (use 15000 for demos)
const unsigned long VACANCY_LIGHT_TIMEOUT = 3UL * 60UL * 1000UL;       // 3 min without person -> auto turn OFF lights
const unsigned long FALLBACK_TIMEOUT = 10UL * 60UL * 1000UL;
// --------------------------------

// Pins
const int PIN_PIR   = 27;   // HC-SR501 / AM312 PIR motion sensor
const int PIN_RADAR = 14;   // Optional secondary radar/ultrasonic sensor (-1 to disable)
const int PIN_DHT   = 4;    // DHT22 data
const int PIN_SDA   = 21;   // BH1750 SDA
const int PIN_SCL   = 22;   // BH1750 SCL
const int PIN_RELAY_LIGHT = 25;
const int PIN_RELAY_FAN   = 26;
const int PIN_RELAY_AC    = 33;   // optional; use a contactor / IR blaster for real ACs
const bool RELAY_ACTIVE_LOW = true;   // most 5V relay boards switch ON when the pin is LOW

DHT dht(PIN_DHT, DHT22);
BH1750 lightMeter;

volatile bool motionLatched = false;
unsigned long lastReport = 0, lastMotionMs = 0;
bool currentLight = false, currentFan = false, currentAc = false;

void IRAM_ATTR onMotion() { motionLatched = true; }

void setRelay(int pin, bool on) {
  bool level = RELAY_ACTIVE_LOW ? !on : on;
  digitalWrite(pin, level ? HIGH : LOW);
}

void applyCommands(bool light, bool fan, bool ac) {
  currentLight = light;
  currentFan = fan;
  currentAc = ac;
  setRelay(PIN_RELAY_LIGHT, light);
  setRelay(PIN_RELAY_FAN, fan);
  setRelay(PIN_RELAY_AC, ac);
  Serial.printf("Relays -> light:%d fan:%d ac:%d\n", light, fan, ac);
}


void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  Serial.println(WiFi.status() == WL_CONNECTED ? "WiFi connected" : "WiFi failed");
}

bool sendReport() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  float lux = lightMeter.readLightLevel();
  if (isnan(t) || isnan(h) || lux < 0) { Serial.println("Sensor read failed"); return false; }

  bool pir = motionLatched || digitalRead(PIN_PIR);
  motionLatched = false;
  if (pir) lastMotionMs = millis();

  connectWifi();
  if (WiFi.status() != WL_CONNECTED) return false;

  JsonDocument doc;
  doc["classroom_id"] = CLASSROOM_ID;
  doc["pir"] = pir ? 1 : 0;
  doc["temp"] = t;
  doc["humidity"] = h;
  doc["lux"] = lux;
  doc["interval_min"] = REPORT_INTERVAL / 60000.0;
  String body; serializeJson(doc, body);

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", API_KEY);
  int code = http.POST(body);
  if (code != 200) { Serial.printf("Server error %d\n", code); http.end(); return false; }

  JsonDocument reply;
  DeserializationError err = deserializeJson(reply, http.getString());
  http.end();
  if (err) return false;

  Serial.printf("P(occupied)=%.2f  %s\n", (float)reply["probability"], (const char*)reply["reason"]);
  applyCommands(reply["light"], reply["fan"], reply["ac"]);
  return true;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_PIR, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_PIR), onMotion, RISING);
  if (PIN_RADAR > 0) pinMode(PIN_RADAR, INPUT);
  pinMode(PIN_RELAY_LIGHT, OUTPUT); pinMode(PIN_RELAY_FAN, OUTPUT); pinMode(PIN_RELAY_AC, OUTPUT);
  applyCommands(false, false, false);          // safe start: everything OFF
  Wire.begin(PIN_SDA, PIN_SCL);
  lightMeter.begin();
  dht.begin();
  connectWifi();
  lastMotionMs = millis();
  lastReport = millis() - REPORT_INTERVAL;     // report immediately on boot
}

void loop() {
  // Real-time sensor presence checking
  bool pirActive = digitalRead(PIN_PIR) == HIGH;
  bool radarActive = (PIN_RADAR > 0) && (digitalRead(PIN_RADAR) == HIGH);
  if (motionLatched || pirActive || radarActive) {
    lastMotionMs = millis();
    motionLatched = true;
  }

  // AUTOMATIC SENSOR-BASED LIGHT SHUTOFF:
  // If lights are ON but sensor detects NO person in the classroom for VACANCY_LIGHT_TIMEOUT,
  // turn OFF the light relay immediately!
  if (currentLight && (millis() - lastMotionMs >= VACANCY_LIGHT_TIMEOUT)) {
    Serial.println("[SENSOR DETECT] No person detected in classroom -> Automatically switching OFF lights!");
    setRelay(PIN_RELAY_LIGHT, false);
    currentLight = false;
    sendReport();  // notify backend and dashboard immediately
  }

  // Periodic report to server
  if (millis() - lastReport >= REPORT_INTERVAL) {
    lastReport = millis();
    if (!sendReport()) {
      // Fail-safe: no server -> simple motion timeout so nothing stays on in an empty room
      if (millis() - lastMotionMs > FALLBACK_TIMEOUT) applyCommands(false, false, false);
    }
  }

  delay(50);
}

