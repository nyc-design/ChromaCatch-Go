/**
 * ChromaCatch-Go ESP32-S3 Firmware v2
 *
 * Multi-mode HID device with e-ink display for mode selection.
 * Supports: BLE Mouse+Keyboard, BLE Gamepad, USB HID (wired).
 * Receives commands over WiFi HTTP or USB Serial.
 *
 * Hardware:
 *   - ESP32-S3 (NimBLE + USB OTG)
 *   - Waveshare e-ink display (SPI)
 *   - 3 buttons: UP (GPIO_UP), DOWN (GPIO_DOWN), SELECT (GPIO_SEL)
 *
 * HTTP API:
 *   GET  /ping       -> {"status": "ok"}
 *   GET  /status     -> full device status including mode
 *   GET  /mode       -> current mode settings
 *   POST /mode       -> update mode settings
 *   POST /command    -> HID command (action + params)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <BleMouse.h>
#include <BleKeyboard.h>
#include <BleGamepad.h>
// E-ink includes would be: #include <GxEPD2_BW.h> -- stubbed for now

// ============================================================
// USER CONFIGURATION -- Edit these before flashing!
// ============================================================
//
// 1. Set your WiFi network name and password below
// 2. Flash:  cd services/esp32 && pio run -t upload
// 3. Open Serial Monitor (115200 baud) to see the IP address
// 4. The device advertises as "ChromaCatch" in your
//    iPhone's Bluetooth settings (Settings > Bluetooth)
//
// After flashing:
//   - Pair "ChromaCatch" on your iPhone
//   - Note the IP address printed on Serial Monitor
//   - Set CC_CLIENT_ESP32_HOST to that IP address
//
const char* WIFI_SSID       = "YOUR_WIFI_SSID";      // <-- Your WiFi network name
const char* WIFI_PASSWORD   = "YOUR_WIFI_PASSWORD";   // <-- Your WiFi password
const int   HTTP_PORT       = 80;
const char* DEVICE_NAME     = "ChromaCatch";

// GPIO pins for buttons (active LOW with internal pull-up)
const int GPIO_UP   = 35;
const int GPIO_DOWN = 34;
const int GPIO_SEL  = 33;
// ============================================================

// ============================================================
// Mode enums
// ============================================================
enum InputMode      { INPUT_WIFI, INPUT_SERIAL };
enum OutputDelivery { OUTPUT_BLUETOOTH, OUTPUT_WIRED };
enum OutputMode     { OUTPUT_MOUSE_KB, OUTPUT_GAMEPAD };

// ============================================================
// Globals
// ============================================================
WebServer server(HTTP_PORT);

// BLE devices (only one active set at a time)
BleMouse*    bleMouse    = nullptr;
BleKeyboard* bleKeyboard = nullptr;
BleGamepad*  bleGamepad  = nullptr;

// Current mode
InputMode      currentInput      = INPUT_WIFI;
OutputDelivery currentOutput     = OUTPUT_BLUETOOTH;
OutputMode     currentOutputMode = OUTPUT_MOUSE_KB;

// Menu state
int menuIndex = 0;
const int MENU_ITEMS = 3;
const char* menuLabels[] = {"Input", "Output", "Mode"};
bool menuActive = true;

// Button debounce
unsigned long lastButtonPress = 0;
const unsigned long DEBOUNCE_MS = 200;

// Serial command buffer (for INPUT_SERIAL mode)
String serialBuffer = "";

// ============================================================
// E-ink display (stubbed -- real implementation uses GxEPD2)
// ============================================================
void displayInit() {
    // In real implementation: GxEPD2_BW display setup
    // Example for Waveshare 2.13" V4:
    //   GxEPD2_BW<GxEPD2_213_BN, GxEPD2_213_BN::HEIGHT> display(
    //       GxEPD2_213_BN(CS, DC, RST, BUSY));
    //   display.init(115200, true, 50, false);
    Serial.println("[E-ink] Display initialized (stub)");
}

void displayMenu() {
    // In real implementation: render to e-ink with partial refresh
    // display.setPartialWindow(0, 0, display.width(), display.height());
    // display.firstPage(); do { ... } while (display.nextPage());

    Serial.println("\n=== ChromaCatch Menu ===");
    for (int i = 0; i < MENU_ITEMS; i++) {
        String prefix = (i == menuIndex) ? "> " : "  ";
        String value;
        switch (i) {
            case 0: value = (currentInput == INPUT_WIFI) ? "WiFi" : "Serial"; break;
            case 1: value = (currentOutput == OUTPUT_BLUETOOTH) ? "Bluetooth" : "Wired"; break;
            case 2: value = (currentOutputMode == OUTPUT_MOUSE_KB) ? "Mouse+KB" : "Gamepad"; break;
        }
        Serial.println(prefix + menuLabels[i] + ": " + value);
    }
    Serial.println("========================");
}

void displayStatus(const char* line1, const char* line2) {
    Serial.println(String("[Status] ") + line1);
    if (line2) Serial.println(String("[Status] ") + line2);
    // In real implementation: render status bar on e-ink
}

// ============================================================
// BLE initialization (tear down old, start new based on mode)
// ============================================================
void initBLE() {
    // Clean up previous instances
    if (bleMouse)    { delete bleMouse;    bleMouse = nullptr; }
    if (bleKeyboard) { delete bleKeyboard; bleKeyboard = nullptr; }
    if (bleGamepad)  { delete bleGamepad;  bleGamepad = nullptr; }

    if (currentOutput != OUTPUT_BLUETOOTH) {
        Serial.println("BLE disabled (wired output mode)");
        return;
    }

    if (currentOutputMode == OUTPUT_MOUSE_KB) {
        bleMouse    = new BleMouse(DEVICE_NAME, "ChromaCatch", 100);
        bleKeyboard = new BleKeyboard(DEVICE_NAME, "ChromaCatch", 100);
        bleMouse->begin();
        bleKeyboard->begin();
        Serial.println("BLE Mouse+Keyboard started");
    } else {
        bleGamepad = new BleGamepad(DEVICE_NAME, "ChromaCatch", 100);
        BleGamepadConfiguration cfg;
        cfg.setAutoReport(false);
        cfg.setButtonCount(16);
        cfg.setHatSwitchCount(1);
        bleGamepad->begin(&cfg);
        Serial.println("BLE Gamepad started");
    }
}

// ============================================================
// BLE connection check
// ============================================================
bool isBLEConnected() {
    if (currentOutput != OUTPUT_BLUETOOTH) return false;
    if (currentOutputMode == OUTPUT_MOUSE_KB) {
        return bleMouse && bleMouse->isConnected();
    }
    return bleGamepad && bleGamepad->isConnected();
}

// ============================================================
// Command execution: Mouse + Keyboard
// ============================================================
void executeMouseCommand(const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!bleMouse || !bleMouse->isConnected()) {
        response["status"] = "error";
        response["error"]  = "BLE mouse not connected";
        return;
    }

    if (action == "move") {
        int dx = doc["dx"] | 0;
        int dy = doc["dy"] | 0;
        bleMouse->move(dx, dy);
        response["status"] = "ok";
        response["dx"] = dx;
        response["dy"] = dy;
    }
    else if (action == "click") {
        int x = doc["x"] | 0;
        int y = doc["y"] | 0;
        // Move to position then click
        bleMouse->move(x, y);
        delay(10);
        bleMouse->click(MOUSE_LEFT);
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
        int steps = max(1, duration / 10);
        int stepX = (x2 - x1) / steps;
        int stepY = (y2 - y1) / steps;

        bleMouse->move(x1, y1);
        delay(10);
        bleMouse->press(MOUSE_LEFT);
        for (int i = 0; i < steps; i++) {
            bleMouse->move(stepX, stepY);
            delay(10);
        }
        bleMouse->release(MOUSE_LEFT);
        response["status"] = "ok";
        response["steps"] = steps;
    }
    else if (action == "press") {
        bleMouse->press(MOUSE_LEFT);
        response["status"] = "ok";
    }
    else if (action == "release") {
        bleMouse->release(MOUSE_LEFT);
        response["status"] = "ok";
    }
    else if (action == "key_press") {
        if (bleKeyboard && bleKeyboard->isConnected()) {
            String key = doc["key"].as<String>();
            if (key.length() == 1) {
                bleKeyboard->press(key[0]);
            }
            response["status"] = "ok";
        } else {
            response["status"] = "error";
            response["error"]  = "BLE keyboard not connected";
        }
    }
    else if (action == "key_release") {
        if (bleKeyboard && bleKeyboard->isConnected()) {
            String key = doc["key"].as<String>();
            if (key.length() == 1) {
                bleKeyboard->release(key[0]);
            }
            response["status"] = "ok";
        } else {
            response["status"] = "error";
            response["error"]  = "BLE keyboard not connected";
        }
    }
    else {
        response["status"] = "error";
        response["error"]  = "unknown mouse/kb action: " + action;
    }
}

// ============================================================
// Command execution: Gamepad
// ============================================================

// Maps string button names to BLE Gamepad button numbers
int mapGamepadButton(const String& name) {
    if (name == "A" || name == "a")                       return 1;
    if (name == "B" || name == "b")                       return 2;
    if (name == "X" || name == "x")                       return 3;
    if (name == "Y" || name == "y")                       return 4;
    if (name == "L" || name == "l" || name == "LB")       return 5;
    if (name == "R" || name == "r" || name == "RB")       return 6;
    if (name == "ZL" || name == "LT")                     return 7;
    if (name == "ZR" || name == "RT")                     return 8;
    if (name == "minus" || name == "select" || name == "MINUS") return 9;
    if (name == "plus"  || name == "start"  || name == "PLUS")  return 10;
    if (name == "L3" || name == "lstick")                 return 11;
    if (name == "R3" || name == "rstick")                 return 12;
    if (name == "home" || name == "HOME")                 return 13;
    if (name == "capture" || name == "CAPTURE")           return 14;
    return 0;  // unknown
}

// D-pad to hat switch mapping (0=N, 1=NE, 2=E, ... 7=NW, -1=center)
int mapDPadToHat(const String& name) {
    if (name == "up"    || name == "DUP")    return 0;  // North
    if (name == "right" || name == "DRIGHT") return 2;  // East
    if (name == "down"  || name == "DDOWN")  return 4;  // South
    if (name == "left"  || name == "DLEFT")  return 6;  // West
    return -1;  // center/release
}

void executeGamepadCommand(const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!bleGamepad || !bleGamepad->isConnected()) {
        response["status"] = "error";
        response["error"]  = "BLE gamepad not connected";
        return;
    }

    if (action == "button_press") {
        String button = doc["button"].as<String>();
        int hat = mapDPadToHat(button);
        if (hat >= 0) {
            bleGamepad->setHat1(hat);
        } else {
            int btn = mapGamepadButton(button);
            if (btn > 0) bleGamepad->press(btn);
        }
        bleGamepad->sendReport();
        response["status"] = "ok";
    }
    else if (action == "button_release") {
        String button = doc["button"].as<String>();
        int hat = mapDPadToHat(button);
        if (hat >= 0) {
            bleGamepad->setHat1(-1);  // center
        } else {
            int btn = mapGamepadButton(button);
            if (btn > 0) bleGamepad->release(btn);
        }
        bleGamepad->sendReport();
        response["status"] = "ok";
    }
    else if (action == "stick") {
        String stick_id = doc["stick_id"].as<String>();
        int x = doc["x"] | 0;
        int y = doc["y"] | 0;
        // Map -32768..32767 -> 0..32767 (BLE Gamepad library range)
        int mappedX = map(x, -32768, 32767, 0, 32767);
        int mappedY = map(y, -32768, 32767, 0, 32767);
        if (stick_id == "left" || stick_id == "LEFT") {
            bleGamepad->setLeftThumb(mappedX, mappedY);
        } else {
            bleGamepad->setRightThumb(mappedX, mappedY);
        }
        bleGamepad->sendReport();
        response["status"] = "ok";
    }
    else {
        response["status"] = "error";
        response["error"]  = "unknown gamepad action: " + action;
    }
}

// ============================================================
// Unified command dispatcher (routes to mouse/kb or gamepad)
// ============================================================
void executeCommand(JsonDocument& doc, JsonDocument& response) {
    String action = doc["action"].as<String>();
    response["action"] = action;

    if (currentOutputMode == OUTPUT_MOUSE_KB) {
        executeMouseCommand(action, doc, response);
    } else {
        executeGamepadCommand(action, doc, response);
    }
}

// ============================================================
// Mode string helpers
// ============================================================
String inputModeStr()      { return currentInput == INPUT_WIFI ? "wifi" : "serial"; }
String outputDeliveryStr() { return currentOutput == OUTPUT_BLUETOOTH ? "bluetooth" : "wired"; }
String outputModeStr()     { return currentOutputMode == OUTPUT_MOUSE_KB ? "mouse_keyboard" : "gamepad"; }

// ============================================================
// HTTP Handlers
// ============================================================
void handlePing() {
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleStatus() {
    JsonDocument doc;
    doc["ble_connected"]   = isBLEConnected();
    doc["ip"]              = WiFi.localIP().toString();
    doc["device_name"]     = DEVICE_NAME;
    doc["input_mode"]      = inputModeStr();
    doc["output_delivery"] = outputDeliveryStr();
    doc["output_mode"]     = outputModeStr();

    String resp;
    serializeJson(doc, resp);
    server.send(200, "application/json", resp);
}

void handleGetMode() {
    JsonDocument doc;
    doc["input_mode"]      = inputModeStr();
    doc["output_delivery"] = outputDeliveryStr();
    doc["output_mode"]     = outputModeStr();

    String resp;
    serializeJson(doc, resp);
    server.send(200, "application/json", resp);
}

void handleSetMode() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    if (deserializeJson(doc, server.arg("plain"))) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    bool changed = false;

    if (doc["input_mode"].is<const char*>()) {
        String val = doc["input_mode"].as<String>();
        InputMode newMode = (val == "serial") ? INPUT_SERIAL : INPUT_WIFI;
        if (newMode != currentInput) { currentInput = newMode; changed = true; }
    }
    if (doc["output_delivery"].is<const char*>()) {
        String val = doc["output_delivery"].as<String>();
        OutputDelivery newDel = (val == "wired") ? OUTPUT_WIRED : OUTPUT_BLUETOOTH;
        if (newDel != currentOutput) { currentOutput = newDel; changed = true; }
    }
    if (doc["output_mode"].is<const char*>()) {
        String val = doc["output_mode"].as<String>();
        OutputMode newMode = (val == "gamepad") ? OUTPUT_GAMEPAD : OUTPUT_MOUSE_KB;
        if (newMode != currentOutputMode) { currentOutputMode = newMode; changed = true; }
    }

    if (changed) {
        initBLE();
        displayMenu();
    }

    // Return current mode as response
    handleGetMode();
}

void handleCommand() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    if (deserializeJson(doc, server.arg("plain"))) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    JsonDocument response;
    executeCommand(doc, response);

    String resp;
    serializeJson(response, resp);
    int code = (response["status"] == "ok") ? 200 : 400;
    server.send(code, "application/json", resp);
}

// ============================================================
// Serial command processing (for INPUT_SERIAL mode)
// ============================================================
void processSerialCommand(const String& line) {
    JsonDocument doc;
    if (deserializeJson(doc, line)) {
        Serial.println("{\"error\":\"invalid json\"}");
        return;
    }

    JsonDocument response;
    executeCommand(doc, response);

    String resp;
    serializeJson(response, resp);
    Serial.println(resp);
}

void handleSerialInput() {
    if (currentInput != INPUT_SERIAL) return;

    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (serialBuffer.length() > 0) {
                processSerialCommand(serialBuffer);
                serialBuffer = "";
            }
        } else {
            serialBuffer += c;
            // Guard against buffer overflow
            if (serialBuffer.length() > 2048) {
                Serial.println("{\"error\":\"serial buffer overflow\"}");
                serialBuffer = "";
            }
        }
    }
}

// ============================================================
// Button handling (physical menu navigation)
// ============================================================
void handleButtons() {
    if (millis() - lastButtonPress < DEBOUNCE_MS) return;

    if (digitalRead(GPIO_UP) == LOW) {
        lastButtonPress = millis();
        menuIndex = (menuIndex - 1 + MENU_ITEMS) % MENU_ITEMS;
        displayMenu();
    }
    else if (digitalRead(GPIO_DOWN) == LOW) {
        lastButtonPress = millis();
        menuIndex = (menuIndex + 1) % MENU_ITEMS;
        displayMenu();
    }
    else if (digitalRead(GPIO_SEL) == LOW) {
        lastButtonPress = millis();
        // Toggle the selected setting
        switch (menuIndex) {
            case 0:
                currentInput = (currentInput == INPUT_WIFI) ? INPUT_SERIAL : INPUT_WIFI;
                break;
            case 1:
                currentOutput = (currentOutput == OUTPUT_BLUETOOTH) ? OUTPUT_WIRED : OUTPUT_BLUETOOTH;
                break;
            case 2:
                currentOutputMode = (currentOutputMode == OUTPUT_MOUSE_KB) ? OUTPUT_GAMEPAD : OUTPUT_MOUSE_KB;
                break;
        }
        initBLE();
        displayMenu();
    }
}

// ============================================================
// WiFi setup
// ============================================================
void setupWiFi() {
    Serial.print("Connecting to WiFi: ");
    Serial.println(WIFI_SSID);
    WiFi.mode(WIFI_STA);
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
        MDNS.begin("chromacatch");
        Serial.println("mDNS: chromacatch.local");
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}

// ============================================================
// Arduino Setup & Loop
// ============================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n=== ChromaCatch-Go ESP32-S3 v2 ===");

    // Button pins (active LOW, internal pull-up)
    pinMode(GPIO_UP,  INPUT_PULLUP);
    pinMode(GPIO_DOWN, INPUT_PULLUP);
    pinMode(GPIO_SEL, INPUT_PULLUP);

    // Display
    displayInit();

    // WiFi first (must be established before BLE on S3)
    setupWiFi();

    // BLE second
    initBLE();

    // HTTP routes (active even in serial input mode for status/mode queries)
    server.on("/ping",    HTTP_GET,  handlePing);
    server.on("/status",  HTTP_GET,  handleStatus);
    server.on("/mode",    HTTP_GET,  handleGetMode);
    server.on("/mode",    HTTP_POST, handleSetMode);
    server.on("/command", HTTP_POST, handleCommand);
    server.begin();

    Serial.println("HTTP server started on port " + String(HTTP_PORT));
    displayMenu();
    Serial.println("Ready for commands!");
}

void loop() {
    // Always handle HTTP (for status/mode even in serial input mode)
    server.handleClient();

    // Physical button menu navigation
    handleButtons();

    // Serial command input (when in serial mode)
    handleSerialInput();

    // Log BLE connection state changes
    static bool lastConnected = false;
    bool connected = isBLEConnected();
    if (connected != lastConnected) {
        Serial.println(connected ? "BLE device connected!" : "BLE device disconnected.");
        displayStatus(connected ? "BLE: Connected" : "BLE: Disconnected", nullptr);
        lastConnected = connected;
    }

    delay(1);
}
