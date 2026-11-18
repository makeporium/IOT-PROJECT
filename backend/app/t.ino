#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

const int SERVO_PIN = 13, TOUCH_PIN = 4, BUZZER_PIN = 14, RED = 25, GREEN = 26, TRIG = 5, ECHO = 18;
const char *TOPIC = "doorsafe/ayush/vehicle_door_demo/actuator/command";
const char *TOUCH = "doorsafe/ayush/vehicle_door_demo/sensor/touch";
const char *RADAR = "doorsafe/ayush/vehicle_door_demo/sensor/radar";
const char *STATE = "doorsafe/ayush/vehicle_door_demo/actuator/state";
WiFiClient net;
PubSubClient mqtt(net);
Servo servo;
bool locked = true, lastTouch = false;
unsigned long unlockedAt = 0, lastRadar = 0, lastSeq = 0;
String lastSid = "", lastId = "";

void toneFor(int hz, int ms)
{
    tone(BUZZER_PIN, hz, ms);
    delay(ms + 35);
}
void openChime()
{
    toneFor(660, 90);
    toneFor(880, 130);
}
void relockChime()
{
    toneFor(420, 100);
    toneFor(300, 150);
}
void dangerAlarm()
{
    toneFor(115, 180);
    toneFor(115, 180);
    toneFor(85, 300);
}
void feedback(const char *id, const char *sid, unsigned long seq, int angle, const char *status)
{
    StaticJsonDocument<256> d;
    d["device_id"] = "esp32_driver_door";
    d["timestamp"] = millis() / 1000.0;
    d["command_id"] = id;
    d["session_id"] = sid;
    d["sequence"] = seq;
    d["requested_angle_deg"] = angle;
    d["applied_angle_deg"] = angle;
    d["door_locked"] = (angle == 0);
    d["status"] = status;
    char o[256];
    serializeJson(d, o);
    mqtt.publish(STATE, o, false);
}
void apply(int angle, bool lock, bool alert, const char *id, const char *sid, unsigned long seq)
{
    locked = lock;
    servo.write(angle);
    digitalWrite(RED, lock);
    digitalWrite(GREEN, !lock);
    if (lock && alert)
        dangerAlarm();
    if (!lock)
    {
        unlockedAt = millis();
        openChime();
    }
    lastId = id;
    lastSid = sid;
    lastSeq = seq;
    feedback(id, sid, seq, angle, lock ? "LOCKED" : "UNLOCKED");
    Serial.printf(lock ? "[DANGER VETO] LOCKED 0 deg # %lu\n" : "[OPEN] UNLOCKED 90 deg: auto re-lock in 3 s # %lu\n", seq);
}
void onMessage(char *, byte *p, unsigned int n)
{
    char b[n + 1];
    memcpy(b, p, n);
    b[n] = 0;
    StaticJsonDocument<384> d;
    if (deserializeJson(d, b))
        return;
    unsigned long seq = d["sequence"] | 0;
    const char *sid = d["session_id"] | "";
    if (lastSid != String(sid))
    {
        lastSid = sid;
        lastSeq = 0;
    }
    if (!seq || seq <= lastSeq)
        return;
    apply(d["servo_angle_deg"] | 0, d["door_locked"] | true, d["chime_alert"] | false, d["command_id"] | "unknown", sid, seq);
}
void connect()
{
    while (!mqtt.connected())
    {
        if (mqtt.connect("doorsafe-ayush-wokwi"))
        {
            mqtt.subscribe(TOPIC);
            Serial.println("[MQTT] ready");
        }
        else
            delay(1000);
    }
}
float distanceCm()
{
    digitalWrite(TRIG, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG, LOW);
    long us = pulseIn(ECHO, HIGH, 35000);
    return us ? us * 0.0343 / 2.0 : 400.0;
}
void publishRadar()
{
    float cm = distanceCm();
    StaticJsonDocument<192> d;
    d["device_id"] = "esp32_door_side_hcsr04";
    d["timestamp"] = millis() / 1000.0;
    d["distance_cm"] = round(cm * 10) / 10.0;
    d["distance_m"] = round(cm) / 100.0;
    d["relative_speed_mps"] = nullptr;
    char o[192];
    serializeJson(d, o);
    mqtt.publish(RADAR, o, false); /* Serial.printf("[HC-SR04] %.1f cm -> dashboard\n",cm); */
}
void publishTouch(bool t)
{
    StaticJsonDocument<128> d;
    d["device_id"] = "esp32_driver_door";
    d["timestamp"] = millis() / 1000.0;
    d["touched"] = t;
    d["raw_capacitive_val"] = t ? 15 : 85;
    char o[128];
    serializeJson(d, o);
    mqtt.publish(TOUCH, o, false);
    Serial.println(t ? "[TOUCH] REQUEST" : "[TOUCH] release");
}
void setup()
{
    Serial.begin(115200);
    pinMode(TOUCH_PIN, INPUT_PULLUP);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(RED, OUTPUT);
    pinMode(GREEN, OUTPUT);
    pinMode(TRIG, OUTPUT);
    pinMode(ECHO, INPUT);
    digitalWrite(RED, HIGH);
    servo.attach(SERVO_PIN);
    servo.write(0);
    WiFi.begin("Wokwi-GUEST", "");
    while (WiFi.status() != WL_CONNECTED)
        delay(100);
    mqtt.setServer("broker.emqx.io", 1883);
    mqtt.setCallback(onMessage);
    mqtt.setBufferSize(512);
    Serial.println("DOORSAFE ready: camera trajectory + HC-SR04 interlock");
}
void loop()
{
    connect();
    mqtt.loop();
    unsigned long now = millis();
    if (now - lastRadar >= 500)
    {
        lastRadar = now;
        publishRadar();
    }
    bool t = digitalRead(TOUCH_PIN) == LOW;
    if (t != lastTouch)
    {
        lastTouch = t;
        publishTouch(t);
    }
    if (!locked && now - unlockedAt >= 3000)
    {
        locked = true;
        servo.write(0);
        digitalWrite(RED, HIGH);
        digitalWrite(GREEN, LOW);
        relockChime();
        feedback(lastId.c_str(), lastSid.c_str(), lastSeq, 0, "AUTO_RELOCKED");
        Serial.println("[AUTO RE-LOCK] 0 deg after 3 s");
    }
}
