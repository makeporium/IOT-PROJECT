/**
 * @file main.cpp
 * @brief DOORSAFE Automotive ESP32 Firmware (C++ / Arduino Framework)
 * @details Implements automotive door latch control via SG90 servo, capacitive
 *          handle touch sensing, ultrasonic / radar ranging, and MQTT IoT telemetry.
 *          Fully compatible with Wokwi simulation and physical ESP32 DevKit.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

// ==========================================
// 1. PIN DEFINITIONS (Wokwi & Hardware Map)
// ==========================================
#define PIN_SERVO        13  // SG90 Servo PWM Control (Door Latch Actuator)
#define PIN_TOUCH_SENSOR 4   // Capacitive Door Handle Touch Pin / Button
#define PIN_BUZZER       14  // Piezoelectric Buzzer (Warning Chime)
#define PIN_LED_RED      25  // RGB Status LED (Red: CRITICAL Lock)
#define PIN_LED_GREEN    26  // RGB Status LED (Green: SAFE Unlatched)
#define PIN_LED_BLUE     27  // RGB Status LED (Blue: Connecting / Standby)
#define PIN_TRIG         5   // HC-SR04 Ultrasonic / Radar Trigger
#define PIN_ECHO         18  // HC-SR04 Ultrasonic / Radar Echo

// ==========================================
// 2. NETWORK & MQTT CONFIGURATION
// ==========================================
// Wokwi simulation uses "Wokwi-GUEST" with empty password
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";

// Public reliable MQTT Broker (matches backend configuration)
const char* MQTT_BROKER   = "broker.emqx.io";
const int   MQTT_PORT     = 1883;
const char* DEVICE_ID     = "esp32_driver_door";

// MQTT Automotive Topic Hierarchy
const char* TOPIC_ACTUATOR_COMMAND = "doorsafe/actuator/command";
const char* TOPIC_HAZARD_TELEMETRY = "doorsafe/telemetry/hazard";
const char* TOPIC_TOUCH            = "doorsafe/sensor/touch";
const char* TOPIC_RADAR            = "doorsafe/sensor/radar";
const char* TOPIC_HEARTBEAT        = "doorsafe/system/heartbeat";

// ==========================================
// 3. GLOBAL OBJECTS & STATE
// ==========================================
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Servo doorServo;

// Actuator & State Variables
int  currentServoAngle = 90; // Default 90 deg (Unlatched / Free)
bool isDoorLocked      = false;
bool buzzerActive      = false;
unsigned long buzzerStartTime = 0;

// Sensor debounce & polling timers
unsigned long lastRadarSampleTime = 0;
unsigned long lastHeartbeatTime   = 0;
unsigned long lastMqttRetryTime   = 0;
bool lastTouchState = false;

// ==========================================
// 4. ACTUATOR & INDICATOR CONTROLLERS
// ==========================================
void setStatusLed(bool r, bool g, bool b) {
    digitalWrite(PIN_LED_RED,   r ? HIGH : LOW);
    digitalWrite(PIN_LED_GREEN, g ? HIGH : LOW);
    digitalWrite(PIN_LED_BLUE,  b ? HIGH : LOW);
}

void setDoorLockActuator(int targetAngle, bool lockEngaged, bool soundChime) {
    currentServoAngle = constrain(targetAngle, 0, 90);
    isDoorLocked = lockEngaged;

    // Direct hardware PWM driving for SG90 servo
    doorServo.write(currentServoAngle);

    if (isDoorLocked || currentServoAngle == 0) {
        // CRITICAL VETO ENGAGED: Solid Red LED
        setStatusLed(true, false, false);
    } else {
        // SAFE / UNLATCHED: Solid Green LED
        setStatusLed(false, true, false);
    }

    if (soundChime) {
        buzzerActive = true;
        buzzerStartTime = millis();
        tone(PIN_BUZZER, 2000); // 2 kHz urgent warning chime
    }
}

// ==========================================
// 5. MQTT INCOMING MESSAGE HANDLER
// ==========================================
void onMqttMessageReceived(char* topic, byte* payload, unsigned int length) {
    char message[length + 1];
    memcpy(message, payload, length);
    message[length] = '\0';

    Serial.printf("[MQTT RX] %s -> %s\n", topic, message);

    // Actuator Command received from backend safety engine
    if (strcmp(topic, TOPIC_ACTUATOR_COMMAND) == 0) {
        StaticJsonDocument<256> doc;
        DeserializationError err = deserializeJson(doc, message);
        if (!err) {
            int angle = doc["servo_angle_deg"] | 90;
            bool locked = doc["door_locked"] | false;
            bool chime = doc["chime_alert"] | false;

            Serial.printf("[ACTUATOR] Target Angle: %d deg | Locked: %s | Chime: %s\n",
                          angle, locked ? "YES" : "NO", chime ? "YES" : "NO");

            // Zero-latency physical hardware actuation
            setDoorLockActuator(angle, locked, chime);
        }
    }
}

// ==========================================
// 6. SENSOR MEASUREMENT ROUTINES
// ==========================================
float readUltrasonicRadarCm() {
    digitalWrite(PIN_TRIG, LOW);
    delayMicroseconds(2);
    digitalWrite(PIN_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_TRIG, LOW);

    long duration = pulseIn(PIN_ECHO, HIGH, 35000); // 35ms timeout (~6m max)
    if (duration == 0) return 400.0; // Out of range default

    float distanceCm = (duration * 0.0343) / 2.0;
    return distanceCm;
}

void pollCapacitiveTouchSensor() {
    // Read capacitive touch pin (on Wokwi push button is LOW when pressed if pulled up)
    int reading = digitalRead(PIN_TOUCH_SENSOR);
    bool isTouched = (reading == LOW); // Pressed = Touch Detected

    if (isTouched != lastTouchState) {
        lastTouchState = isTouched;

        if (isTouched) {
            Serial.println("[SENSOR] Capacitive Handle Touch Detected! Publishing to MQTT...");
            
            StaticJsonDocument<256> doc;
            doc["device_id"] = DEVICE_ID;
            doc["timestamp"] = millis() / 1000.0;
            doc["touched"] = true;
            doc["raw_capacitive_val"] = 15; // Simulated raw ADC value

            char buffer[256];
            serializeJson(doc, buffer);
            mqttClient.publish(TOPIC_TOUCH, buffer, true);
        }
    }
}

// ==========================================
// 7. NETWORK & TELEMETRY LIFECYCLE
// ==========================================
void connectWiFi() {
    Serial.printf("[WIFI] Connecting to %s...", WIFI_SSID);
    setStatusLed(false, false, true); // Blue = WiFi connecting

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(300);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
        setStatusLed(false, true, false); // Green
    } else {
        Serial.println("\n[WIFI] Connection timed out. Running in standalone fallback mode.");
    }
}

void connectMQTT() {
    if (WiFi.status() != WL_CONNECTED) return;
    if (mqttClient.connected()) return;

    unsigned long now = millis();
    if (now - lastMqttRetryTime < 4000) return; // Retry every 4s
    lastMqttRetryTime = now;

    Serial.printf("[MQTT] Connecting to broker %s:%d...\n", MQTT_BROKER, MQTT_PORT);

    // Last Will and Testament (LWT) payload
    StaticJsonDocument<128> lwtDoc;
    lwtDoc["device_id"] = DEVICE_ID;
    lwtDoc["status"] = "OFFLINE";
    char lwtBuf[128];
    serializeJson(lwtDoc, lwtBuf);

    if (mqttClient.connect(DEVICE_ID, TOPIC_HEARTBEAT, 1, false, lwtBuf)) {
        Serial.println("[MQTT] Broker Connected Successfully!");
        setStatusLed(false, true, false);

        // Subscribe to safety actuator commands
        mqttClient.subscribe(TOPIC_ACTUATOR_COMMAND, 1);
        mqttClient.subscribe(TOPIC_HAZARD_TELEMETRY, 0);

        // Emit initial online announcement
        StaticJsonDocument<192> bootDoc;
        bootDoc["device_id"] = DEVICE_ID;
        bootDoc["status"] = "ONLINE";
        bootDoc["uptime_seconds"] = millis() / 1000;
        bootDoc["firmware_version"] = "1.0.0";
        bootDoc["free_heap_bytes"] = ESP.getFreeHeap();
        char bootBuf[192];
        serializeJson(bootDoc, bootBuf);
        mqttClient.publish(TOPIC_HEARTBEAT, bootBuf);
    } else {
        Serial.printf("[MQTT] Connection failed, rc=%d. Will retry.\n", mqttClient.state());
    }
}

// ==========================================
// 8. SETUP & MAIN LOOP
// ==========================================
void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println("==================================================");
    Serial.println("  DOORSAFE Automotive ESP32 Firmware v1.0.0");
    Serial.println("  Hardware: ESP32 DevKit + SG90 + Touch + Ultrasonic");
    Serial.println("==================================================");

    // Initialize GPIO pins
    pinMode(PIN_TOUCH_SENSOR, INPUT_PULLUP);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_BLUE, OUTPUT);
    pinMode(PIN_TRIG, OUTPUT);
    pinMode(PIN_ECHO, INPUT);

    // Initialize SG90 Servo on PWM pin 13
    ESP32PWM::allocateTimer(0);
    doorServo.setPeriodHertz(50);
    doorServo.attach(PIN_SERVO, 500, 2400); // 500us to 2400us pulses

    // Default to unlatched 90 deg position
    setDoorLockActuator(90, false, false);

    // Setup WiFi & MQTT
    connectWiFi();
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(onMqttMessageReceived);
    mqttClient.setBufferSize(512);
}

void loop() {
    // 1. Maintain network loops
    if (WiFi.status() == WL_CONNECTED) {
        if (!mqttClient.connected()) {
            connectMQTT();
        } else {
            mqttClient.loop();
        }
    }

    // 2. Poll capacitive handle touch sensor
    pollCapacitiveTouchSensor();

    // 3. Turn off warning chime after 600ms pulse
    if (buzzerActive && (millis() - buzzerStartTime > 600)) {
        noTone(PIN_BUZZER);
        buzzerActive = false;
    }

    // 4. Sample and broadcast ultrasonic radar distance every 500ms
    unsigned long now = millis();
    if (now - lastRadarSampleTime >= 500) {
        lastRadarSampleTime = now;
        float distCm = readUltrasonicRadarCm();

        if (mqttClient.connected()) {
            StaticJsonDocument<192> radarDoc;
            radarDoc["device_id"] = "esp32_rear_radar";
            radarDoc["timestamp"] = now / 1000.0;
            radarDoc["distance_cm"] = round(distCm * 10.0) / 10.0;
            radarDoc["distance_m"] = round((distCm / 100.0) * 100.0) / 100.0;

            char buf[192];
            serializeJson(radarDoc, buf);
            mqttClient.publish(TOPIC_RADAR, buf, false);
        }
    }

    // 5. Emit health monitor heartbeat every 10 seconds
    if (now - lastHeartbeatTime >= 10000) {
        lastHeartbeatTime = now;
        if (mqttClient.connected()) {
            StaticJsonDocument<192> hbDoc;
            hbDoc["device_id"] = DEVICE_ID;
            hbDoc["status"] = isDoorLocked ? "CRITICAL_LOCK" : "ONLINE";
            hbDoc["uptime_seconds"] = now / 1000;
            hbDoc["free_heap_bytes"] = ESP.getFreeHeap();
            hbDoc["firmware_version"] = "1.0.0";

            char hbBuf[192];
            serializeJson(hbDoc, hbBuf);
            mqttClient.publish(TOPIC_HEARTBEAT, hbBuf, false);
        }
    }

    delay(10); // FreeRTOS yield
}
