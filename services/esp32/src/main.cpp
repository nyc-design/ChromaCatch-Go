/**
 * ChromaCatch-Go ESP32-S3 Firmware v3
 *
 * Goals:
 *  - Accept commands from BOTH WiFi (HTTP) and wired (USB Serial) concurrently
 *  - Prioritize wired commands when both are active
 *  - Support packaged HID modes for automation
 *  - Auto-select HID output transport: USB (if host mounted) else BLE (if connected)
 *  - Expose Waveshare e-ink display APIs
 *  - Accept command input over WebSocket with HTTP fallback
 *
 * Modes:
 *  1) combo                  (keyboard + mouse)
 *  2) keyboard_only
 *  3) mouse_only
 *  4) gamepad
 *  5) switch_controller
 *
 * HTTP API:
 *   GET  /ping
 *   GET  /status
 *   GET  /mode
 *   POST /mode
 *   POST /command
 *   GET  /display
 *   POST /display
 *   POST /display/clear
 *
 * WebSocket API:
 *   ws://<ip>:81
 *   JSON command in:  {"type":"command","seq":1,"action":"move","dx":1,"dy":2}
 *   JSON response out: {"type":"ack","seq":1,"status":"ok",...}
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>

#include <NimBLEDevice.h>
#include <BleCombo.h>
#include <BleGamepad.h>

#include "usb_hid_bridge.h"

// ============================================================
// USER CONFIGURATION -- Edit before flashing
// ============================================================
const char* WIFI_SSID       = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD   = "YOUR_WIFI_PASSWORD";
const int   HTTP_PORT       = 80;
const int   WS_PORT         = 81;
const char* DEVICE_NAME     = "ChromaCatch";

// GPIO pins for 3-button menu (active LOW with pull-up)
const int GPIO_UP   = 35;
const int GPIO_DOWN = 34;
const int GPIO_SEL  = 33;

// Waveshare e-ink SPI pins (update for your wiring)
const int EPD_CS   = 10;
const int EPD_DC   = 9;
const int EPD_RST  = 8;
const int EPD_BUSY = 7;

// Wired command priority window. During this window, WiFi /command is deferred.
const unsigned long WIRED_PRIORITY_WINDOW_MS = 250;
const unsigned long WS_PRIORITY_WINDOW_MS = 200;

// ============================================================
// Mode enums
// ============================================================
enum DeviceMode {
    MODE_COMBO = 0,
    MODE_KEYBOARD_ONLY,
    MODE_MOUSE_ONLY,
    MODE_GAMEPAD,
    MODE_SWITCH_CONTROLLER,
    MODE_COUNT
};

enum DeliveryPolicy {
    DELIVERY_AUTO = 0,       // USB if available, else BLE
    DELIVERY_FORCE_USB,
    DELIVERY_FORCE_BLE,
};

enum RuntimeDelivery {
    RUNTIME_NONE = 0,
    RUNTIME_USB,
    RUNTIME_BLE,
};

enum CommandSource {
    SOURCE_WIFI = 0,
    SOURCE_WEBSOCKET,
    SOURCE_WIRED,
};

// ============================================================
// Globals
// ============================================================
WebServer server(HTTP_PORT);
WebSocketsServer wsServer(WS_PORT);

// BLE outputs (created/destroyed on mode changes)
BleComboKeyboard* bleComboKb = nullptr;
BleComboMouse*    bleComboMouse = nullptr;
BleGamepad*       bleGamepad = nullptr;

DeviceMode currentMode = MODE_COMBO;
DeliveryPolicy deliveryPolicy = DELIVERY_AUTO;

String serialBuffer = "";
unsigned long lastWiredCommandAtMs = 0;
unsigned long lastWSCommandAtMs = 0;
uint32_t wsMsgCounter = 0;
size_t wsConnectedClients = 0;

// Menu
int menuIndex = 0;
const int MENU_ITEMS = 2;
const char* menuLabels[] = {"Mode", "Delivery"};
unsigned long lastButtonPress = 0;
const unsigned long DEBOUNCE_MS = 200;

// Display state (stubbed renderer)
struct DisplayState {
    String line1;
    String line2;
    String line3;
    bool sticky = true;
    unsigned long expiresAtMs = 0;
};
DisplayState displayState;
bool einkReady = false;
GxEPD2_BW<GxEPD2_213_BN, GxEPD2_213_BN::HEIGHT> eink(GxEPD2_213_BN(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

// ============================================================
// Helpers
// ============================================================
bool strEqIgnoreCase(const String& a, const char* b) {
    String rhs = String(b);
    return a.equalsIgnoreCase(rhs);
}

int8_t clampInt8(int value) {
    if (value > 127) return 127;
    if (value < -127) return -127;
    return static_cast<int8_t>(value);
}

bool isSwitchMode(DeviceMode mode) {
    return mode == MODE_SWITCH_CONTROLLER;
}

bool modeAllowsMouse(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_MOUSE_ONLY;
}

bool modeAllowsKeyboard(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY;
}

bool modeAllowsGamepad(DeviceMode mode) {
    return mode == MODE_GAMEPAD || mode == MODE_SWITCH_CONTROLLER;
}

bool modeUsesBleOutput(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY || mode == MODE_MOUSE_ONLY || mode == MODE_GAMEPAD || mode == MODE_SWITCH_CONTROLLER;
}

bool modeUsesBleCombo(DeviceMode mode) {
    return mode == MODE_COMBO || mode == MODE_KEYBOARD_ONLY || mode == MODE_MOUSE_ONLY;
}

bool modeUsesBleGamepad(DeviceMode mode) {
    return mode == MODE_GAMEPAD || mode == MODE_SWITCH_CONTROLLER;
}

const char* modeToString(DeviceMode mode) {
    switch (mode) {
        case MODE_COMBO: return "combo";
        case MODE_KEYBOARD_ONLY: return "keyboard_only";
        case MODE_MOUSE_ONLY: return "mouse_only";
        case MODE_GAMEPAD: return "gamepad";
        case MODE_SWITCH_CONTROLLER: return "switch_controller";
        default: return "combo";
    }
}

const char* deliveryPolicyToString(DeliveryPolicy p) {
    switch (p) {
        case DELIVERY_AUTO: return "auto";
        case DELIVERY_FORCE_USB: return "wired";
        case DELIVERY_FORCE_BLE: return "bluetooth";
        default: return "auto";
    }
}

const char* runtimeDeliveryToString(RuntimeDelivery d) {
    switch (d) {
        case RUNTIME_USB: return "wired";
        case RUNTIME_BLE: return "bluetooth";
        default: return "none";
    }
}

bool wiredPriorityActive() {
    return (millis() - lastWiredCommandAtMs) <= WIRED_PRIORITY_WINDOW_MS;
}

bool wsPriorityActive() {
    return (millis() - lastWSCommandAtMs) <= WS_PRIORITY_WINDOW_MS;
}

DeviceMode parseModeString(const String& raw) {
    if (raw.length() == 0) return currentMode;

    if (raw == "combo" || raw == "mouse_keyboard") return MODE_COMBO;
    if (raw == "keyboard" || raw == "keyboard_only") return MODE_KEYBOARD_ONLY;
    if (raw == "mouse" || raw == "mouse_only") return MODE_MOUSE_ONLY;
    if (raw == "gamepad" || raw == "general_gamepad") return MODE_GAMEPAD;
    if (raw == "switch" || raw == "switch_pro" || raw == "switch_controller") return MODE_SWITCH_CONTROLLER;
    // Legacy compatibility aliases for removed BT-input mode.
    if (raw == "switch_wired_bt_input" || raw == "switch_controller_wired_bt_input" || raw == "switch_wired") return MODE_SWITCH_CONTROLLER;
    return currentMode;
}

DeliveryPolicy parseDeliveryPolicy(const String& raw) {
    if (raw == "wired" || raw == "usb" || raw == "force_wired") return DELIVERY_FORCE_USB;
    if (raw == "bluetooth" || raw == "ble" || raw == "force_bluetooth") return DELIVERY_FORCE_BLE;
    return DELIVERY_AUTO;
}

// ============================================================
// E-ink display (Waveshare via GxEPD2)
// ============================================================
void displayInit() {
    Serial.println("[E-ink] Initializing...");
    eink.init(115200, true, 50, false);
    eink.setRotation(1);
    eink.setTextColor(GxEPD_BLACK);
    eink.setFont(&FreeMonoBold9pt7b);
    einkReady = true;
    Serial.println("[E-ink] Initialized");
}

void renderDisplayNow() {
    Serial.println("\n=== E-ink ===");
    Serial.println(displayState.line1);
    Serial.println(displayState.line2);
    Serial.println(displayState.line3);
    Serial.println("=============");

    if (!einkReady) return;

    eink.setFullWindow();
    eink.firstPage();
    do {
        eink.fillScreen(GxEPD_WHITE);
        eink.setCursor(2, 22);
        eink.print(displayState.line1);
        eink.setCursor(2, 50);
        eink.print(displayState.line2);
        eink.setCursor(2, 78);
        eink.print(displayState.line3);
    } while (eink.nextPage());
}

void displaySet(const String& l1, const String& l2 = "", const String& l3 = "", bool sticky = true, unsigned long ttlMs = 0) {
    displayState.line1 = l1;
    displayState.line2 = l2;
    displayState.line3 = l3;
    displayState.sticky = sticky;
    displayState.expiresAtMs = sticky ? 0 : (millis() + ttlMs);
    renderDisplayNow();
}

void displayClear() {
    displayState.line1 = "";
    displayState.line2 = "";
    displayState.line3 = "";
    displayState.sticky = true;
    displayState.expiresAtMs = 0;
    renderDisplayNow();
}

void displayMenu() {
    String mode = String(modeToString(currentMode));
    String policy = String(deliveryPolicyToString(deliveryPolicy));

    Serial.println("\n=== ChromaCatch Menu ===");
    for (int i = 0; i < MENU_ITEMS; i++) {
        String prefix = (i == menuIndex) ? "> " : "  ";
        if (i == 0) {
            Serial.println(prefix + String(menuLabels[i]) + ": " + mode);
        } else {
            Serial.println(prefix + String(menuLabels[i]) + ": " + policy);
        }
    }
    Serial.println("========================");

    displaySet(mode, "delivery=" + policy, "wired>ws>http", true, 0);
}

void displayStatus(const String& l1, const String& l2 = "", const String& l3 = "") {
    displaySet(l1, l2, l3, false, 1500);
}

void updateDisplayExpiry() {
    if (!displayState.sticky && displayState.expiresAtMs > 0 && millis() >= displayState.expiresAtMs) {
        displayMenu();
    }
}

// ============================================================
// USB HID init & state
// ============================================================
void initUSBHID() {
    UsbHidBridge::init();
    Serial.println("USB HID initialized");
}

bool isUSBMounted() {
    return UsbHidBridge::isMounted();
}

// ============================================================
// BLE init and state
// ============================================================
void stopBLE() {
    if (bleComboMouse) { delete bleComboMouse; bleComboMouse = nullptr; }
    if (bleComboKb)    { delete bleComboKb;    bleComboKb = nullptr; }
    if (bleGamepad)    { delete bleGamepad;    bleGamepad = nullptr; }
}

void initBLE() {
    stopBLE();

    if (!modeUsesBleOutput(currentMode)) {
        Serial.println("BLE output disabled for current mode");
        return;
    }

    if (modeUsesBleCombo(currentMode)) {
        bleComboKb = new BleComboKeyboard(DEVICE_NAME, "ChromaCatch", 100);
        bleComboMouse = new BleComboMouse(bleComboKb);
        bleComboKb->begin();
        Serial.println("BLE combo (keyboard+mouse) started");
        return;
    }

    if (modeUsesBleGamepad(currentMode)) {
        bleGamepad = new BleGamepad(DEVICE_NAME, "ChromaCatch", 100);
        BleGamepadConfiguration cfg;
        cfg.setAutoReport(false);
        cfg.setButtonCount(16);
        cfg.setHatSwitchCount(1);
        bleGamepad->begin(&cfg);
        Serial.println("BLE gamepad started");
        return;
    }
}

bool isBLEConnected() {
    if (modeUsesBleCombo(currentMode)) {
        return bleComboKb && bleComboKb->isConnected();
    }
    if (modeUsesBleGamepad(currentMode)) {
        return bleGamepad && bleGamepad->isConnected();
    }
    return false;
}

RuntimeDelivery chooseRuntimeDelivery() {
    bool usbAvailable = isUSBMounted();
    bool bleAvailable = isBLEConnected();

    switch (deliveryPolicy) {
        case DELIVERY_FORCE_USB:
            return usbAvailable ? RUNTIME_USB : RUNTIME_NONE;
        case DELIVERY_FORCE_BLE:
            return bleAvailable ? RUNTIME_BLE : RUNTIME_NONE;
        case DELIVERY_AUTO:
        default:
            if (usbAvailable) return RUNTIME_USB;
            if (bleAvailable) return RUNTIME_BLE;
            return RUNTIME_NONE;
    }
}

// ============================================================
// HID key helpers
// ============================================================
uint8_t parseKeyCode(const String& key) {
    if (key.length() == 1) return static_cast<uint8_t>(key[0]);

    if (strEqIgnoreCase(key, "enter") || strEqIgnoreCase(key, "return")) return KEY_RETURN;
    if (strEqIgnoreCase(key, "esc") || strEqIgnoreCase(key, "escape")) return KEY_ESC;
    if (strEqIgnoreCase(key, "tab")) return KEY_TAB;
    if (strEqIgnoreCase(key, "space")) return ' ';
    if (strEqIgnoreCase(key, "up")) return KEY_UP_ARROW;
    if (strEqIgnoreCase(key, "down")) return KEY_DOWN_ARROW;
    if (strEqIgnoreCase(key, "left")) return KEY_LEFT_ARROW;
    if (strEqIgnoreCase(key, "right")) return KEY_RIGHT_ARROW;
    if (strEqIgnoreCase(key, "backspace")) return KEY_BACKSPACE;
    if (strEqIgnoreCase(key, "delete")) return KEY_DELETE;
    if (strEqIgnoreCase(key, "home")) return KEY_HOME;
    if (strEqIgnoreCase(key, "end")) return KEY_END;
    if (strEqIgnoreCase(key, "page_up")) return KEY_PAGE_UP;
    if (strEqIgnoreCase(key, "page_down")) return KEY_PAGE_DOWN;

    return 0;
}

uint8_t parseMouseButton(JsonDocument& doc) {
    String name = doc["button"].as<String>();
    if (name.length() == 0 || strEqIgnoreCase(name, "left")) return UsbHidBridge::USB_MOUSE_BTN_LEFT;
    if (strEqIgnoreCase(name, "right")) return UsbHidBridge::USB_MOUSE_BTN_RIGHT;
    if (strEqIgnoreCase(name, "middle")) return UsbHidBridge::USB_MOUSE_BTN_MIDDLE;
    return UsbHidBridge::USB_MOUSE_BTN_LEFT;
}

int mapDPadToBleHat(const String& name) {
    if (strEqIgnoreCase(name, "up") || strEqIgnoreCase(name, "DUP")) return 0;
    if (strEqIgnoreCase(name, "right") || strEqIgnoreCase(name, "DRIGHT")) return 2;
    if (strEqIgnoreCase(name, "down") || strEqIgnoreCase(name, "DDOWN")) return 4;
    if (strEqIgnoreCase(name, "left") || strEqIgnoreCase(name, "DLEFT")) return 6;
    return -1;
}

uint8_t mapDPadToUsbHat(const String& name) {
    if (strEqIgnoreCase(name, "up") || strEqIgnoreCase(name, "DUP")) return UsbHidBridge::USB_HAT_UP;
    if (strEqIgnoreCase(name, "right") || strEqIgnoreCase(name, "DRIGHT")) return UsbHidBridge::USB_HAT_RIGHT;
    if (strEqIgnoreCase(name, "down") || strEqIgnoreCase(name, "DDOWN")) return UsbHidBridge::USB_HAT_DOWN;
    if (strEqIgnoreCase(name, "left") || strEqIgnoreCase(name, "DLEFT")) return UsbHidBridge::USB_HAT_LEFT;
    return UsbHidBridge::USB_HAT_CENTER;
}

int mapGamepadButtonBLE(const String& name, bool switchLayout) {
    // BLE gamepad lib uses button numbers 1..N
    if (strEqIgnoreCase(name, "A")) return switchLayout ? 2 : 1;
    if (strEqIgnoreCase(name, "B")) return switchLayout ? 1 : 2;
    if (strEqIgnoreCase(name, "X")) return switchLayout ? 4 : 3;
    if (strEqIgnoreCase(name, "Y")) return switchLayout ? 3 : 4;

    if (strEqIgnoreCase(name, "L") || strEqIgnoreCase(name, "LB")) return 5;
    if (strEqIgnoreCase(name, "R") || strEqIgnoreCase(name, "RB")) return 6;
    if (strEqIgnoreCase(name, "ZL") || strEqIgnoreCase(name, "LT")) return 7;
    if (strEqIgnoreCase(name, "ZR") || strEqIgnoreCase(name, "RT")) return 8;
    if (strEqIgnoreCase(name, "minus") || strEqIgnoreCase(name, "select")) return 9;
    if (strEqIgnoreCase(name, "plus") || strEqIgnoreCase(name, "start")) return 10;
    if (strEqIgnoreCase(name, "L3") || strEqIgnoreCase(name, "lstick")) return 11;
    if (strEqIgnoreCase(name, "R3") || strEqIgnoreCase(name, "rstick")) return 12;
    if (strEqIgnoreCase(name, "home")) return 13;
    if (strEqIgnoreCase(name, "capture")) return 14;

    return 0;
}

uint8_t mapGamepadButtonUSB(const String& name, bool switchLayout) {
    // USB gamepad buttons are 0-based symbolic constants.
    if (strEqIgnoreCase(name, "A")) return switchLayout ? UsbHidBridge::USB_BUTTON_EAST : UsbHidBridge::USB_BUTTON_SOUTH;
    if (strEqIgnoreCase(name, "B")) return switchLayout ? UsbHidBridge::USB_BUTTON_SOUTH : UsbHidBridge::USB_BUTTON_EAST;
    if (strEqIgnoreCase(name, "X")) return switchLayout ? UsbHidBridge::USB_BUTTON_NORTH : UsbHidBridge::USB_BUTTON_WEST;
    if (strEqIgnoreCase(name, "Y")) return switchLayout ? UsbHidBridge::USB_BUTTON_WEST : UsbHidBridge::USB_BUTTON_NORTH;

    if (strEqIgnoreCase(name, "L") || strEqIgnoreCase(name, "LB")) return UsbHidBridge::USB_BUTTON_TL;
    if (strEqIgnoreCase(name, "R") || strEqIgnoreCase(name, "RB")) return UsbHidBridge::USB_BUTTON_TR;
    if (strEqIgnoreCase(name, "ZL") || strEqIgnoreCase(name, "LT")) return UsbHidBridge::USB_BUTTON_TL2;
    if (strEqIgnoreCase(name, "ZR") || strEqIgnoreCase(name, "RT")) return UsbHidBridge::USB_BUTTON_TR2;
    if (strEqIgnoreCase(name, "minus") || strEqIgnoreCase(name, "select")) return UsbHidBridge::USB_BUTTON_SELECT;
    if (strEqIgnoreCase(name, "plus") || strEqIgnoreCase(name, "start")) return UsbHidBridge::USB_BUTTON_START;
    if (strEqIgnoreCase(name, "home")) return UsbHidBridge::USB_BUTTON_MODE;
    if (strEqIgnoreCase(name, "L3") || strEqIgnoreCase(name, "lstick")) return UsbHidBridge::USB_BUTTON_THUMBL;
    if (strEqIgnoreCase(name, "R3") || strEqIgnoreCase(name, "rstick")) return UsbHidBridge::USB_BUTTON_THUMBR;

    // No dedicated capture in generic USB gamepad descriptor.
    if (strEqIgnoreCase(name, "capture")) return UsbHidBridge::USB_BUTTON_MODE;

    return 0xFF;
}

// ============================================================
// Command execution by category & transport
// ============================================================
void executeMouseCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsMouse(currentMode)) {
        response["status"] = "error";
        response["error"] = "mouse actions not allowed in current mode";
        return;
    }

    if (transport == RUNTIME_BLE) {
        if (!bleComboKb || !bleComboKb->isConnected() || !bleComboMouse) {
            response["status"] = "error";
            response["error"] = "BLE combo not connected";
            return;
        }

        if (action == "move") {
            int dx = doc["dx"] | 0;
            int dy = doc["dy"] | 0;
            bleComboMouse->move(dx, dy);
            response["status"] = "ok";
            return;
        }

        if (action == "click") {
            if (doc["x"].is<int>() || doc["y"].is<int>()) {
                int x = doc["x"] | 0;
                int y = doc["y"] | 0;
                bleComboMouse->move(x, y);
                delay(5);
            }
            bleComboMouse->click(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "swipe") {
            int x1 = doc["x1"] | 0;
            int y1 = doc["y1"] | 0;
            int x2 = doc["x2"] | 0;
            int y2 = doc["y2"] | 0;
            int duration = doc["duration_ms"] | 300;
            int steps = max(1, duration / 10);
            int stepX = (x2 - x1) / steps;
            int stepY = (y2 - y1) / steps;

            bleComboMouse->move(x1, y1);
            delay(5);
            bleComboMouse->press(parseMouseButton(doc));
            for (int i = 0; i < steps; i++) {
                bleComboMouse->move(stepX, stepY);
                delay(10);
            }
            bleComboMouse->release(parseMouseButton(doc));
            response["status"] = "ok";
            response["steps"] = steps;
            return;
        }

        if (action == "press") {
            bleComboMouse->press(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "release") {
            bleComboMouse->release(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }
    }

    if (transport == RUNTIME_USB) {
        if (action == "move") {
            int dx = doc["dx"] | 0;
            int dy = doc["dy"] | 0;
            UsbHidBridge::mouseMove(dx, dy);
            response["status"] = "ok";
            return;
        }

        if (action == "click") {
            if (doc["x"].is<int>() || doc["y"].is<int>()) {
                int x = doc["x"] | 0;
                int y = doc["y"] | 0;
                UsbHidBridge::mouseMove(x, y);
                delay(5);
            }
            UsbHidBridge::mouseClick(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "swipe") {
            int x1 = doc["x1"] | 0;
            int y1 = doc["y1"] | 0;
            int x2 = doc["x2"] | 0;
            int y2 = doc["y2"] | 0;
            int duration = doc["duration_ms"] | 300;
            int steps = max(1, duration / 10);
            int stepX = (x2 - x1) / steps;
            int stepY = (y2 - y1) / steps;

            UsbHidBridge::mouseMove(x1, y1);
            delay(5);
            UsbHidBridge::mousePress(parseMouseButton(doc));
            for (int i = 0; i < steps; i++) {
                UsbHidBridge::mouseMove(stepX, stepY);
                delay(10);
            }
            UsbHidBridge::mouseRelease(parseMouseButton(doc));
            response["status"] = "ok";
            response["steps"] = steps;
            return;
        }

        if (action == "press") {
            UsbHidBridge::mousePress(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }

        if (action == "release") {
            UsbHidBridge::mouseRelease(parseMouseButton(doc));
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown mouse action";
}

void executeKeyboardCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsKeyboard(currentMode)) {
        response["status"] = "error";
        response["error"] = "keyboard actions not allowed in current mode";
        return;
    }

    if (action == "text") {
        String text = doc["text"].as<String>();
        if (transport == RUNTIME_BLE) {
            if (!bleComboKb || !bleComboKb->isConnected()) {
                response["status"] = "error";
                response["error"] = "BLE combo not connected";
                return;
            }
            bleComboKb->print(text);
        } else {
            UsbHidBridge::keyboardPrint(text);
        }
        response["status"] = "ok";
        response["count"] = text.length();
        return;
    }

    String keyName = doc["key"].as<String>();
    uint8_t keyCode = parseKeyCode(keyName);
    if (keyCode == 0) {
        response["status"] = "error";
        response["error"] = "invalid key";
        return;
    }

    if (transport == RUNTIME_BLE) {
        if (!bleComboKb || !bleComboKb->isConnected()) {
            response["status"] = "error";
            response["error"] = "BLE combo not connected";
            return;
        }

        if (action == "key_press") {
            bleComboKb->press(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_release") {
            bleComboKb->release(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_tap") {
            bleComboKb->press(keyCode);
            delay(10);
            bleComboKb->release(keyCode);
            response["status"] = "ok";
            return;
        }
    } else {
        if (action == "key_press") {
            UsbHidBridge::keyboardPress(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_release") {
            UsbHidBridge::keyboardRelease(keyCode);
            response["status"] = "ok";
            return;
        }
        if (action == "key_tap") {
            UsbHidBridge::keyboardPress(keyCode);
            delay(10);
            UsbHidBridge::keyboardRelease(keyCode);
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown keyboard action";
}

void executeGamepadCommand(RuntimeDelivery transport, const String& action, JsonDocument& doc, JsonDocument& response) {
    if (!modeAllowsGamepad(currentMode)) {
        response["status"] = "error";
        response["error"] = "gamepad actions not allowed in current mode";
        return;
    }

    bool switchLayout = isSwitchMode(currentMode);

    if (transport == RUNTIME_BLE) {
        if (!bleGamepad || !bleGamepad->isConnected()) {
            response["status"] = "error";
            response["error"] = "BLE gamepad not connected";
            return;
        }

        if (action == "button_press") {
            String button = doc["button"].as<String>();
            int hat = mapDPadToBleHat(button);
            if (hat >= 0) {
                bleGamepad->setHat1(hat);
            } else {
                int btn = mapGamepadButtonBLE(button, switchLayout);
                if (btn <= 0) {
                    response["status"] = "error";
                    response["error"] = "unknown button";
                    return;
                }
                bleGamepad->press(btn);
            }
            bleGamepad->sendReport();
            response["status"] = "ok";
            return;
        }

        if (action == "button_release") {
            String button = doc["button"].as<String>();
            int hat = mapDPadToBleHat(button);
            if (hat >= 0) {
                bleGamepad->setHat1(-1);
            } else {
                int btn = mapGamepadButtonBLE(button, switchLayout);
                if (btn <= 0) {
                    response["status"] = "error";
                    response["error"] = "unknown button";
                    return;
                }
                bleGamepad->release(btn);
            }
            bleGamepad->sendReport();
            response["status"] = "ok";
            return;
        }

        if (action == "stick") {
            String stick = doc["stick_id"].as<String>();
            int x = doc["x"] | 0;
            int y = doc["y"] | 0;
            int mappedX = map(x, -32768, 32767, 0, 32767);
            int mappedY = map(y, -32768, 32767, 0, 32767);
            if (strEqIgnoreCase(stick, "left")) {
                bleGamepad->setLeftThumb(mappedX, mappedY);
            } else {
                bleGamepad->setRightThumb(mappedX, mappedY);
            }
            bleGamepad->sendReport();
            response["status"] = "ok";
            return;
        }

        if (action == "hat") {
            String dir = doc["direction"].as<String>();
            int hat = mapDPadToBleHat(dir);
            bleGamepad->setHat1(hat >= 0 ? hat : -1);
            bleGamepad->sendReport();
            response["status"] = "ok";
            return;
        }
    }

    if (transport == RUNTIME_USB) {
        if (action == "button_press") {
            String button = doc["button"].as<String>();
            uint8_t hat = mapDPadToUsbHat(button);
            if (hat != UsbHidBridge::USB_HAT_CENTER || strEqIgnoreCase(button, "up") || strEqIgnoreCase(button, "down") || strEqIgnoreCase(button, "left") || strEqIgnoreCase(button, "right") || strEqIgnoreCase(button, "DUP") || strEqIgnoreCase(button, "DDOWN") || strEqIgnoreCase(button, "DLEFT") || strEqIgnoreCase(button, "DRIGHT")) {
                UsbHidBridge::gamepadHat(hat);
                response["status"] = "ok";
                return;
            }

            uint8_t btn = mapGamepadButtonUSB(button, switchLayout);
            if (btn == 0xFF) {
                response["status"] = "error";
                response["error"] = "unknown button";
                return;
            }
            UsbHidBridge::gamepadPress(btn);
            response["status"] = "ok";
            return;
        }

        if (action == "button_release") {
            String button = doc["button"].as<String>();
            uint8_t hat = mapDPadToUsbHat(button);
            if (hat != UsbHidBridge::USB_HAT_CENTER || strEqIgnoreCase(button, "up") || strEqIgnoreCase(button, "down") || strEqIgnoreCase(button, "left") || strEqIgnoreCase(button, "right") || strEqIgnoreCase(button, "DUP") || strEqIgnoreCase(button, "DDOWN") || strEqIgnoreCase(button, "DLEFT") || strEqIgnoreCase(button, "DRIGHT")) {
                UsbHidBridge::gamepadHat(UsbHidBridge::USB_HAT_CENTER);
                response["status"] = "ok";
                return;
            }

            uint8_t btn = mapGamepadButtonUSB(button, switchLayout);
            if (btn == 0xFF) {
                response["status"] = "error";
                response["error"] = "unknown button";
                return;
            }
            UsbHidBridge::gamepadRelease(btn);
            response["status"] = "ok";
            return;
        }

        if (action == "stick") {
            String stick = doc["stick_id"].as<String>();
            int x = doc["x"] | 0;
            int y = doc["y"] | 0;
            int8_t mappedX = clampInt8(map(x, -32768, 32767, -127, 127));
            int8_t mappedY = clampInt8(map(y, -32768, 32767, -127, 127));
            if (strEqIgnoreCase(stick, "left")) {
                UsbHidBridge::gamepadLeftStick(mappedX, mappedY);
            } else {
                UsbHidBridge::gamepadRightStick(mappedX, mappedY);
            }
            response["status"] = "ok";
            return;
        }

        if (action == "hat") {
            String dir = doc["direction"].as<String>();
            uint8_t hat = mapDPadToUsbHat(dir);
            UsbHidBridge::gamepadHat(hat);
            response["status"] = "ok";
            return;
        }
    }

    response["status"] = "error";
    response["error"] = "unknown gamepad action";
}

void executeCommand(JsonDocument& doc, JsonDocument& response, CommandSource source) {
    String action = doc["action"].as<String>();
    response["action"] = action;
    response["mode"] = modeToString(currentMode);
    if (source == SOURCE_WIRED) response["source"] = "wired";
    else if (source == SOURCE_WEBSOCKET) response["source"] = "websocket";
    else response["source"] = "wifi";

    // Control-plane actions (do not require active HID output transport)
    if (action == "set_mode") {
        if (doc["mode"].is<const char*>()) {
            currentMode = parseModeString(doc["mode"].as<String>());
            initBLE();
            displayMenu();
        }
        response["status"] = "ok";
        response["mode"] = modeToString(currentMode);
        response["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
        return;
    }
    if (action == "set_delivery_policy") {
        if (doc["delivery_policy"].is<const char*>()) {
            deliveryPolicy = parseDeliveryPolicy(doc["delivery_policy"].as<String>());
            displayMenu();
        } else if (doc["policy"].is<const char*>()) {
            deliveryPolicy = parseDeliveryPolicy(doc["policy"].as<String>());
            displayMenu();
        } else if (doc["value"].is<const char*>()) {
            deliveryPolicy = parseDeliveryPolicy(doc["value"].as<String>());
            displayMenu();
        }
        response["status"] = "ok";
        response["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
        response["mode"] = modeToString(currentMode);
        return;
    }

    RuntimeDelivery delivery = chooseRuntimeDelivery();
    response["delivery"] = runtimeDeliveryToString(delivery);

    if (delivery == RUNTIME_NONE) {
        response["status"] = "error";
        response["error"] = "no available output transport (USB not mounted and BLE not connected)";
        return;
    }

    if (action == "move" || action == "click" || action == "swipe" || action == "press" || action == "release") {
        executeMouseCommand(delivery, action, doc, response);
    }
    else if (action == "key_press" || action == "key_release" || action == "key_tap" || action == "text") {
        executeKeyboardCommand(delivery, action, doc, response);
    }
    else if (action == "button_press" || action == "button_release" || action == "stick" || action == "hat") {
        executeGamepadCommand(delivery, action, doc, response);
    }
    else {
        response["status"] = "error";
        response["error"] = "unknown action";
    }
}

// ============================================================
// HTTP handlers
// ============================================================
void sendJson(int code, JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    server.send(code, "application/json", out);
}

void handlePing() {
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleStatus() {
    JsonDocument doc;
    doc["device_name"] = DEVICE_NAME;
    doc["ip"] = WiFi.localIP().toString();
    doc["mode"] = modeToString(currentMode);
    doc["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
    doc["active_delivery"] = runtimeDeliveryToString(chooseRuntimeDelivery());
    doc["usb_mounted"] = isUSBMounted();
    doc["ble_connected"] = isBLEConnected();
    doc["wired_priority_active"] = wiredPriorityActive();
    doc["ws_priority_active"] = wsPriorityActive();
    doc["wired_priority_window_ms"] = WIRED_PRIORITY_WINDOW_MS;
    doc["ws_priority_window_ms"] = WS_PRIORITY_WINDOW_MS;
    doc["ws_port"] = WS_PORT;
    doc["ws_connected_clients"] = wsConnectedClients;
    sendJson(200, doc);
}

void handleGetMode() {
    JsonDocument doc;
    doc["mode"] = modeToString(currentMode);
    doc["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
    // Backward-compatible fields
    doc["output_mode"] = (modeAllowsGamepad(currentMode) ? "gamepad" : "mouse_keyboard");
    doc["output_delivery"] = deliveryPolicyToString(deliveryPolicy);
    doc["input_mode"] = "auto_dual";
    sendJson(200, doc);
}

void applyModeChange(bool modeChanged) {
    if (modeChanged) {
        initBLE();
        displayMenu();
    }
}

void handleSetMode() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    DeviceMode nextMode = currentMode;
    DeliveryPolicy nextPolicy = deliveryPolicy;

    if (doc["mode"].is<const char*>()) {
        nextMode = parseModeString(doc["mode"].as<String>());
    }
    // compatibility with existing callers
    if (doc["hid_mode"].is<const char*>()) {
        nextMode = parseModeString(doc["hid_mode"].as<String>());
    }
    if (doc["output_mode"].is<const char*>()) {
        nextMode = parseModeString(doc["output_mode"].as<String>());
    }

    if (doc["delivery_policy"].is<const char*>()) {
        nextPolicy = parseDeliveryPolicy(doc["delivery_policy"].as<String>());
    }
    if (doc["output_delivery"].is<const char*>()) {
        nextPolicy = parseDeliveryPolicy(doc["output_delivery"].as<String>());
    }

    bool changed = (nextMode != currentMode) || (nextPolicy != deliveryPolicy);
    currentMode = nextMode;
    deliveryPolicy = nextPolicy;

    applyModeChange(changed);
    handleGetMode();
}

void handleDisplayGet() {
    JsonDocument doc;
    doc["line1"] = displayState.line1;
    doc["line2"] = displayState.line2;
    doc["line3"] = displayState.line3;
    doc["sticky"] = displayState.sticky;
    sendJson(200, doc);
}

void handleDisplaySet() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    String l1 = doc["line1"].as<String>();
    String l2 = doc["line2"].as<String>();
    String l3 = doc["line3"].as<String>();
    bool sticky = doc["sticky"].is<bool>() ? doc["sticky"].as<bool>() : true;
    unsigned long ttlMs = doc["ttl_ms"] | 1500;

    displaySet(l1, l2, l3, sticky, ttlMs);

    JsonDocument response;
    response["status"] = "ok";
    response["line1"] = l1;
    sendJson(200, response);
}

void handleDisplayClear() {
    displayClear();
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleCommand() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"error\":\"no body\"}");
        return;
    }

    if (wiredPriorityActive()) {
        JsonDocument deferred;
        deferred["status"] = "deferred";
        deferred["reason"] = "wired_priority_window_active";
        deferred["window_ms"] = WIRED_PRIORITY_WINDOW_MS;
        sendJson(409, deferred);
        return;
    }
    if (wsPriorityActive()) {
        JsonDocument deferred;
        deferred["status"] = "deferred";
        deferred["reason"] = "websocket_priority_window_active";
        deferred["window_ms"] = WS_PRIORITY_WINDOW_MS;
        sendJson(409, deferred);
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (err) {
        server.send(400, "application/json", "{\"error\":\"invalid json\"}");
        return;
    }

    JsonDocument response;
    executeCommand(doc, response, SOURCE_WIFI);

    int code = 400;
    String status = response["status"].as<String>();
    if (status == "ok") code = 200;
    else if (status == "deferred") code = 409;

    sendJson(code, response);
}

// ============================================================
// WebSocket command input (priority: wired > websocket > http)
// ============================================================
void sendWSJson(uint8_t clientId, JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    wsServer.sendTXT(clientId, out);
}

void processWSCommand(uint8_t clientId, JsonDocument& doc) {
    JsonDocument response;

    if (wiredPriorityActive()) {
        response["type"] = "ack";
        response["status"] = "deferred";
        response["reason"] = "wired_priority_window_active";
        response["window_ms"] = WIRED_PRIORITY_WINDOW_MS;
        if (doc["seq"].is<uint32_t>()) response["seq"] = doc["seq"].as<uint32_t>();
        sendWSJson(clientId, response);
        return;
    }

    lastWSCommandAtMs = millis();
    executeCommand(doc, response, SOURCE_WEBSOCKET);
    response["type"] = "ack";
    if (doc["seq"].is<uint32_t>()) response["seq"] = doc["seq"].as<uint32_t>();
    sendWSJson(clientId, response);
}

void onWSEvent(uint8_t clientId, WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED: {
            wsConnectedClients = wsServer.connectedClients();
            Serial.printf("[WS] Client %u connected (%u total)\n", clientId, static_cast<unsigned>(wsConnectedClients));
            JsonDocument hello;
            hello["type"] = "hello";
            hello["status"] = "ok";
            hello["mode"] = modeToString(currentMode);
            hello["delivery_policy"] = deliveryPolicyToString(deliveryPolicy);
            hello["wired_priority_window_ms"] = WIRED_PRIORITY_WINDOW_MS;
            hello["ws_priority_window_ms"] = WS_PRIORITY_WINDOW_MS;
            sendWSJson(clientId, hello);
            break;
        }
        case WStype_DISCONNECTED:
            wsConnectedClients = wsServer.connectedClients();
            Serial.printf("[WS] Client %u disconnected (%u total)\n", clientId, static_cast<unsigned>(wsConnectedClients));
            break;
        case WStype_TEXT: {
            wsMsgCounter++;
            JsonDocument doc;
            DeserializationError err = deserializeJson(doc, payload, length);
            if (err) {
                JsonDocument bad;
                bad["type"] = "ack";
                bad["status"] = "error";
                bad["error"] = "invalid json";
                sendWSJson(clientId, bad);
                return;
            }

            String msgType = doc["type"].as<String>();
            if (msgType == "ping") {
                JsonDocument pong;
                pong["type"] = "pong";
                if (doc["seq"].is<uint32_t>()) pong["seq"] = doc["seq"].as<uint32_t>();
                pong["counter"] = wsMsgCounter;
                sendWSJson(clientId, pong);
                return;
            }

            if (doc["action"].is<const char*>()) {
                processWSCommand(clientId, doc);
                return;
            }

            JsonDocument unsupported;
            unsupported["type"] = "ack";
            unsupported["status"] = "error";
            unsupported["error"] = "missing action";
            if (doc["seq"].is<uint32_t>()) unsupported["seq"] = doc["seq"].as<uint32_t>();
            sendWSJson(clientId, unsupported);
            break;
        }
        default:
            break;
    }
}

// ============================================================
// Serial input handling (wired command channel)
// ============================================================
void processSerialCommand(const String& line) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, line);
    if (err) {
        Serial.println("{\"status\":\"error\",\"error\":\"invalid json\"}");
        return;
    }

    lastWiredCommandAtMs = millis();

    JsonDocument response;
    executeCommand(doc, response, SOURCE_WIRED);

    String out;
    serializeJson(response, out);
    Serial.println(out);
}

void handleSerialInput() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (serialBuffer.length() > 0) {
                processSerialCommand(serialBuffer);
                serialBuffer = "";
            }
        } else {
            serialBuffer += c;
            if (serialBuffer.length() > 2048) {
                Serial.println("{\"status\":\"error\",\"error\":\"serial buffer overflow\"}");
                serialBuffer = "";
            }
        }
    }
}

// ============================================================
// Buttons / menu
// ============================================================
void cycleModeForward() {
    int next = (static_cast<int>(currentMode) + 1) % static_cast<int>(MODE_COUNT);
    currentMode = static_cast<DeviceMode>(next);
    initBLE();
    displayMenu();
}

void cycleDeliveryPolicyForward() {
    int next = (static_cast<int>(deliveryPolicy) + 1) % 3;
    deliveryPolicy = static_cast<DeliveryPolicy>(next);
    displayMenu();
}

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
        if (menuIndex == 0) cycleModeForward();
        else cycleDeliveryPolicyForward();
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
        if (MDNS.begin("chromacatch")) {
            Serial.println("mDNS: chromacatch.local");
        }
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}

// ============================================================
// Setup / Loop
// ============================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n=== ChromaCatch-Go ESP32-S3 v3 ===");

    pinMode(GPIO_UP, INPUT_PULLUP);
    pinMode(GPIO_DOWN, INPUT_PULLUP);
    pinMode(GPIO_SEL, INPUT_PULLUP);

    displayInit();

    setupWiFi();
    initUSBHID();
    initBLE();

    server.on("/ping", HTTP_GET, handlePing);
    server.on("/status", HTTP_GET, handleStatus);
    server.on("/mode", HTTP_GET, handleGetMode);
    server.on("/mode", HTTP_POST, handleSetMode);
    server.on("/command", HTTP_POST, handleCommand);
    server.on("/display", HTTP_GET, handleDisplayGet);
    server.on("/display", HTTP_POST, handleDisplaySet);
    server.on("/display/clear", HTTP_POST, handleDisplayClear);
    server.begin();
    wsServer.begin();
    wsServer.onEvent(onWSEvent);

    Serial.println("HTTP server started on port " + String(HTTP_PORT));
    Serial.println("WebSocket server started on port " + String(WS_PORT));
    displayMenu();
    Serial.println("Ready for commands (wired > websocket > http)");
}

void loop() {
    server.handleClient();
    wsServer.loop();
    handleButtons();
    handleSerialInput();
    updateDisplayExpiry();

    static bool lastBle = false;
    static bool lastUsb = false;

    bool bleNow = isBLEConnected();
    bool usbNow = isUSBMounted();

    if (bleNow != lastBle) {
        Serial.println(bleNow ? "BLE connected" : "BLE disconnected");
        displayStatus(bleNow ? "BLE connected" : "BLE disconnected");
        lastBle = bleNow;
    }

    if (usbNow != lastUsb) {
        Serial.println(usbNow ? "USB host mounted" : "USB host unmounted");
        displayStatus(usbNow ? "USB mounted" : "USB unmounted");
        lastUsb = usbNow;
    }

    delay(1);
}
