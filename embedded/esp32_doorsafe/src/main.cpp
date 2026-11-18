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
const char* TOPIC_ACTUATOR_COMMAND = "doorsafe/ayush/vehicle_door_demo_v2/actuator/command";
const char* TOPIC_ACTUATOR_STATE   = "doorsafe/ayush/vehicle_door_demo_v2/actuator/state";
const char* TOPIC_HAZARD_TELEMETRY = "doorsafe/ayush/vehicle_door_demo_v2/telemetry/hazard";
const char* TOPIC_TOUCH            = "doorsafe/ayush/vehicle_door_demo_v2/sensor/touch";
const char* TOPIC_RADAR            = "doorsafe/ayush/vehicle_door_demo_v2/sensor/radar";
const char* TOPIC_HEARTBEAT        = "doorsafe/ayush/vehicle_door_demo_v2/system/heartbeat";

// ==========================================
// 3. GLOBAL OBJECTS & STATE
// ==========================================
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Servo doorServo;

// Actuator & State Variables
int  currentServoAngle = 0; // Fail-safe boot position
bool isDoorLocked      = true;

// Door experience: a successful request opens for three seconds, then the
// latch returns to its fail-safe locked position automatically.
const unsigned long AUTO_RELOCK_DELAY_MS = 3000;
bool autoRelockPending = false;
unsigned long autoRelockAt = 0;
String lastActuatorCommandId = "auto-relock";
String lastActuatorSessionId = "local-controller";
unsigned long lastActuatorSequence = 0;

// Non-blocking audio patterns keep MQTT and the touch sensor responsive.
enum SoundPattern { SOUND_NONE, SOUND_OPEN, SOUND_CLOSE, SOUND_DANGER };
SoundPattern activeSound = SOUND_NONE;
uint8_t soundStep = 0;
unsigned long soundStepStartedAt = 0;

// Sensor debounce & polling timers
unsigned long lastRadarSampleTime = 0;
unsigned long lastHeartbeatTime   = 0;
unsigned long lastMqttRetryTime   = 0;
unsigned long lastWifiRetryTime   = 0;
bool lastTouchState = false;
unsigned long lastMqttConnectedTime = 0;
unsigned long lastAppliedSequence = 0;
String lastCommandSession = "";

void publishActuatorState(
    const char* commandId,
    const char* sessionId,
    unsigned long sequence,
    int requestedAngle,
    const char* status
) {
    if (!mqttClient.connected()) return;
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis() / 1000.0;
    doc["command_id"] = commandId;
    doc["session_id"] = sessionId;
    doc["sequence"] = sequence;
    doc["requested_angle_deg"] = requestedAngle;
    doc["applied_angle_deg"] = currentServoAngle;
    doc["door_locked"] = isDoorLocked;
    doc["status"] = status;
    char buffer[256];
    serializeJson(doc, buffer);
    mqttClient.publish(TOPIC_ACTUATOR_STATE, buffer, false);
}

// ==========================================
// 4. ACTUATOR & INDICATOR CONTROLLERS
// ==========================================
void setStatusLed(bool r, bool g, bool b) {
    digitalWrite(PIN_LED_RED,   r ? HIGH : LOW);
    digitalWrite(PIN_LED_GREEN, g ? HIGH : LOW);
    digitalWrite(PIN_LED_BLUE,  b ? HIGH : LOW);
}

void setDoorLockActuator(int targetAngle, bool lockEngaged) {
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

}

void stopSound() {
    noTone(PIN_BUZZER);
    activeSound = SOUND_NONE;
    soundStep = 0;
}

void startSound(SoundPattern pattern) {
    stopSound();
    activeSound = pattern;
    soundStepStartedAt = millis();

    if (pattern == SOUND_OPEN) {
        tone(PIN_BUZZER, 980);       // gentle rising confirmation
        Serial.println("[SOUND] OPEN confirmation");
    } else if (pattern == SOUND_CLOSE) {
        tone(PIN_BUZZER, 1450);      // gentle descending confirmation
        Serial.println("[SOUND] CLOSE confirmation");
    } else if (pattern == SOUND_DANGER) {
        tone(PIN_BUZZER, 230);       // blunt low-frequency safety veto
        Serial.println("[SOUND] DANGER veto alarm");
    }
}

void updateSound() {
    if (activeSound == SOUND_NONE) return;
    unsigned long elapsed = millis() - soundStepStartedAt;

    if (activeSound == SOUND_OPEN) {
        if (soundStep == 0 && elapsed >= 90) {
            tone(PIN_BUZZER, 1480);
            soundStep = 1;
            soundStepStartedAt = millis();
        } else if (soundStep == 1 && elapsed >= 140) {
            stopSound();
        }
        return;
    }

    if (activeSound == SOUND_CLOSE) {
        if (soundStep == 0 && elapsed >= 90) {
            tone(PIN_BUZZER, 850);
            soundStep = 1;
            soundStepStartedAt = millis();
        } else if (soundStep == 1 && elapsed >= 160) {
            stopSound();
        }
        return;
    }

    // Three blunt low pulses are unmistakably different from open/close.
    if (activeSound == SOUND_DANGER) {
        if (soundStep == 0 && elapsed >= 220) {
            noTone(PIN_BUZZER);
            soundStep = 1;
            soundStepStartedAt = millis();
        } else if (soundStep == 1 && elapsed >= 90) {
            tone(PIN_BUZZER, 190);
            soundStep = 2;
            soundStepStartedAt = millis();
        } else if (soundStep == 2 && elapsed >= 220) {
            noTone(PIN_BUZZER);
            soundStep = 3;
            soundStepStartedAt = millis();
        } else if (soundStep == 3 && elapsed >= 90) {
            tone(PIN_BUZZER, 160);
            soundStep = 4;
            soundStepStartedAt = millis();
        } else if (soundStep == 4 && elapsed >= 350) {
            stopSound();
        }
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
            const char* command = doc["command"] | "UNKNOWN";
            const char* commandId = doc["command_id"] | "missing-command-id";
            const char* sessionId = doc["session_id"] | "missing-session-id";
            unsigned long sequence = doc["sequence"] | 0;

            if (lastCommandSession != String(sessionId)) {
                lastCommandSession = String(sessionId);
                lastAppliedSequence = 0;
            }

            if (sequence == 0 || sequence <= lastAppliedSequence) {
                Serial.printf("[ACTUATOR] Rejected stale command sequence %lu\n", sequence);
                publishActuatorState(commandId, sessionId, sequence, angle, "REJECTED");
                return;
            }

            Serial.printf("[ACTUATOR] Target Angle: %d deg | Locked: %s | Chime: %s\n",
                          angle, locked ? "YES" : "NO", chime ? "YES" : "NO");

            // Zero-latency physical hardware actuation.
            setDoorLockActuator(angle, locked);
            lastAppliedSequence = sequence;
            lastActuatorCommandId = String(commandId);
            lastActuatorSessionId = String(sessionId);
            lastActuatorSequence = sequence;

            if (!locked && angle >= 90) {
                autoRelockPending = true;
                autoRelockAt = millis() + AUTO_RELOCK_DELAY_MS;
                startSound(SOUND_OPEN);
                Serial.printf("[OPEN] UNLOCKED 90 deg: auto re-lock in 3 s # %lu\n", sequence);
            } else {
                autoRelockPending = false;
                // Frame-level danger updates are silent. A blocked handle
                // request explicitly carries chime_alert=true.
                if (chime && (strcmp(command, "LOCK_ENGAGED") == 0 ||
                              strcmp(command, "FAIL_SAFE_LOCK") == 0)) {
                    startSound(SOUND_DANGER);
                    Serial.printf("[DANGER VETO] LOCKED 0 deg # %lu\n", sequence);
                }
            }
            publishActuatorState(commandId, sessionId, sequence, angle, "APPLIED");
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

        {
            Serial.printf("[SENSOR] Capacitive Handle state: %s\n", isTouched ? "TOUCHED" : "RELEASED");
            
            StaticJsonDocument<256> doc;
            doc["device_id"] = DEVICE_ID;
            doc["timestamp"] = millis() / 1000.0;
            doc["touched"] = isTouched;
            doc["raw_capacitive_val"] = isTouched ? 15 : 85;

            char buffer[256];
            serializeJson(doc, buffer);
            // Touch is an edge event and must never be retained.
            bool published = mqttClient.connected() && mqttClient.publish(TOPIC_TOUCH, buffer, false);
            Serial.printf("[TOUCH MQTT] %s\n", published ? "sent" : "not connected");
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
    while (WiFi.status() != WL_CONNECTED && attempts < 50) {
        delay(300);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
        setStatusLed(false, true, false); // Green
    } else {
        Serial.println("\n[WIFI] Initial connection timed out. Background retry enabled.");
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

    // A fixed broker client ID makes multiple Wokwi/browser sessions kick
    // each other offline. Keep the payload DEVICE_ID stable, but use a unique
    // connection ID on every simulator boot.
    char mqttClientId[64];
    snprintf(
        mqttClientId,
        sizeof(mqttClientId),
        "doorsafe_esp32_%08lx_%08lx",
        (unsigned long)(ESP.getEfuseMac() & 0xffffffffULL),
        (unsigned long)esp_random()
    );

    if (mqttClient.connect(mqttClientId, TOPIC_HEARTBEAT, 1, false, lwtBuf)) {
        Serial.println("[MQTT] Broker Connected Successfully!");
        setStatusLed(false, true, false);

        // Subscribe to safety actuator commands
        mqttClient.subscribe(TOPIC_ACTUATOR_COMMAND, 1);
        // The actuator controller only needs actuator commands. Subscribing to
        // frame-by-frame hazard telemetry flooded the serial console and could
        // starve command handling when a browser tab was throttled.

        // Emit initial online announcement
        StaticJsonDocument<192> bootDoc;
        bootDoc["device_id"] = DEVICE_ID;
        bootDoc["timestamp"] = millis() / 1000.0;
        bootDoc["status"] = "ONLINE";
        bootDoc["uptime_seconds"] = millis() / 1000;
        bootDoc["firmware_version"] = "1.0.0";
        bootDoc["free_heap_bytes"] = ESP.getFreeHeap();
        char bootBuf[192];
        serializeJson(bootDoc, bootBuf);
        mqttClient.publish(TOPIC_HEARTBEAT, bootBuf);
        lastMqttConnectedTime = millis();
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

    // Fail-safe default: remain locked until a fresh validated command arrives.
    setDoorLockActuator(0, true);

    // Setup WiFi & MQTT
    connectWiFi();
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(onMqttMessageReceived);
    mqttClient.setBufferSize(512);
}

void loop() {
    // 1. Maintain network loops
    if (WiFi.status() != WL_CONNECTED && millis() - lastWifiRetryTime >= 5000) {
        lastWifiRetryTime = millis();
        Serial.println("[WIFI] Retrying Wokwi-GUEST...");
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }

    if (WiFi.status() == WL_CONNECTED) {
        if (!mqttClient.connected()) {
            connectMQTT();
        } else {
            mqttClient.loop();
            lastMqttConnectedTime = millis();
        }
    }

    // Communication watchdog: loss of the command bus always returns to lock.
    if (!mqttClient.connected() && millis() - lastMqttConnectedTime > 2000 && !isDoorLocked) {
        autoRelockPending = false;
        setDoorLockActuator(0, true);
        startSound(SOUND_DANGER);
        Serial.println("[FAIL-SAFE] MQTT unavailable: door lock engaged.");
    }

    // 2. Poll capacitive handle touch sensor
    pollCapacitiveTouchSensor();

    // 3. Complete non-blocking sound patterns and automatic relock.
    updateSound();
    if (autoRelockPending && (long)(millis() - autoRelockAt) >= 0) {
        autoRelockPending = false;
        setDoorLockActuator(0, true);
        startSound(SOUND_CLOSE);
        Serial.println("[AUTO RE-LOCK] servo returned to 0 deg after 3 s");
        publishActuatorState(
            lastActuatorCommandId.c_str(),
            lastActuatorSessionId.c_str(),
            lastActuatorSequence,
            0,
            "AUTO_RELOCKED"
        );
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

    // 5. Emit health monitor heartbeat every 3 seconds
    if (now - lastHeartbeatTime >= 3000) {
        lastHeartbeatTime = now;
        if (mqttClient.connected()) {
            StaticJsonDocument<192> hbDoc;
            hbDoc["device_id"] = DEVICE_ID;
            hbDoc["timestamp"] = now / 1000.0;
            hbDoc["status"] = isDoorLocked ? "CRITICAL_LOCK" : "ONLINE";
            hbDoc["uptime_seconds"] = now / 1000;
            hbDoc["free_heap_bytes"] = ESP.getFreeHeap();
            hbDoc["firmware_version"] = "1.1.0";

            char hbBuf[192];
            serializeJson(hbDoc, hbBuf);
            mqttClient.publish(TOPIC_HEARTBEAT, hbBuf, false);
        }
    }

    delay(10); // FreeRTOS yield
}
