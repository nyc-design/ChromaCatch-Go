/**
 * ChromaCatch-Go ESP32 Firmware
 *
 * BLE HID Mouse emulation + WiFi HTTP command server.
 *
 * The ESP32 advertises as a Bluetooth mouse. When paired with an iPhone,
 * it can send mouse move/click/swipe events. Commands are received over
 * WiFi HTTP from the local airplay client.
 *
 * HTTP API:
 *   GET  /ping        → {"status": "ok"}
 *   GET  /status      → {"ble_connected": bool, "ip": str, "device_name": str}
 *   POST /command      → {"action": "move|click|swipe|press|release", ...params}
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <BleCombo.h>  // ESP32-BLE-Combo for mouse+keyboard HID

// ============================================================
// USER CONFIGURATION -- Edit these before flashing!
// ============================================================
//
// 1. Set your WiFi network name and password below
// 2. Flash:  cd services/esp32 && pio run -t upload
// 3. Open Serial Monitor (115200 baud) to see the IP address
// 4. The device advertises as "ChromaCatch Mouse" in your
//    iPhone's Bluetooth settings (Settings > Bluetooth)
//
// After flashing:
//   - Pair "ChromaCatch Mouse" on your iPhone
//   - Note the IP address printed on Serial Monitor
//   - Set CC_CLIENT_ESP32_HOST to that IP address
//
const char* WIFI_SSID       = "YOUR_WIFI_SSID";     // <-- Your WiFi network name
const char* WIFI_PASSWORD   = "YOUR_WIFI_PASSWORD";  // <-- Your WiFi password
const int   HTTP_PORT       = 80;
const char* BLE_DEVICE_NAME = "ChromaCatch Mouse";
// ============================================================

// ---- Globals ----
WebServer server(HTTP_PORT);
bool bleConnected = false;

// ---- BLE HID Setup ----
void setupBLE() {
    Serial.println("Starting BLE HID Mouse...");
    Keyboard.begin();  // BleCombo initializes both keyboard and mouse
    Mouse.begin();
    Serial.println("BLE HID Mouse advertising as: " + String(BLE_DEVICE_NAME));
}

// ---- WiFi Setup ----
void setupWiFi() {
    Serial.print("Connecting to WiFi: ");
    Serial.println(WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nWiFi connection failed! Commands will not work.");
    }
}

// ---- HTTP Handlers ----

void handlePing() {
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleStatus() {
    JsonDocument doc;
    doc["ble_connected"] = Keyboard.isConnected();
    doc["ip"] = WiFi.localIP().toString();
    doc["device_name"] = BLE_DEVICE_NAME;

    String response;
    serializeJson(doc, response);
    server.send(200, "application/json", response);
}

void handleCommand() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, server.arg("plain"));
    if (error) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    String action = doc["action"].as<String>();

    if (!Keyboard.isConnected()) {
        server.send(503, "application/json", "{\"error\":\"BLE not connected\"}");
        return;
    }

    JsonDocument response;
    response["action"] = action;

    if (action == "move") {
        int dx = doc["dx"] | 0;
        int dy = doc["dy"] | 0;
        Mouse.move(dx, dy);
        response["status"] = "ok";
        response["dx"] = dx;
        response["dy"] = dy;
    }
    else if (action == "click") {
        int x = doc["x"] | 0;
        int y = doc["y"] | 0;
        // Move to position then click
        Mouse.move(x, y);
        delay(10);
        Mouse.click(MOUSE_LEFT);
        response["status"] = "ok";
        response["x"] = x;
        response["y"] = y;
    }
    else if (action == "swipe") {
        int x1 = doc["x1"] | 0;
        int y1 = doc["y1"] | 0;
        int x2 = doc["x2"] | 0;
        int y2 = doc["y2"] | 0;
        int duration = doc["duration_ms"] | 300;

        // Calculate steps based on duration (roughly 10ms per step)
        int steps = duration / 10;
        if (steps < 1) steps = 1;
        int stepX = (x2 - x1) / steps;
        int stepY = (y2 - y1) / steps;

        Mouse.move(x1, y1);
        delay(10);
        Mouse.press(MOUSE_LEFT);
        for (int i = 0; i < steps; i++) {
            Mouse.move(stepX, stepY);
            delay(10);
        }
        Mouse.release(MOUSE_LEFT);

        response["status"] = "ok";
        response["steps"] = steps;
    }
    else if (action == "press") {
        Mouse.press(MOUSE_LEFT);
        response["status"] = "ok";
    }
    else if (action == "release") {
        Mouse.release(MOUSE_LEFT);
        response["status"] = "ok";
    }
    else {
        response["status"] = "error";
        response["error"] = "unknown action: " + action;
        String resp;
        serializeJson(response, resp);
        server.send(400, "application/json", resp);
        return;
    }

    String resp;
    serializeJson(response, resp);
    server.send(200, "application/json", resp);
}

// ---- Arduino Setup & Loop ----

void setup() {
    Serial.begin(115200);
    Serial.println("\n=== ChromaCatch-Go ESP32 ===");

    setupBLE();
    setupWiFi();

    // Register HTTP routes
    server.on("/ping", HTTP_GET, handlePing);
    server.on("/status", HTTP_GET, handleStatus);
    server.on("/command", HTTP_POST, handleCommand);
    server.begin();

    Serial.println("HTTP server started on port " + String(HTTP_PORT));
    Serial.println("Ready for commands!");
}

void loop() {
    server.handleClient();

    // Periodically log BLE connection state changes
    static bool lastConnected = false;
    bool connected = Keyboard.isConnected();
    if (connected != lastConnected) {
        Serial.println(connected ? "BLE device connected!" : "BLE device disconnected.");
        lastConnected = connected;
    }

    delay(1);
}
