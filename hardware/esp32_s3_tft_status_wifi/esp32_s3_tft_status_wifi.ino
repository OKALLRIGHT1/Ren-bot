#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "local_secrets.h"

static const int TFT_CS = 8;
static const int TFT_DC = 9;
static const int TFT_RST = 10;
static const int TFT_MOSI = 11;
static const int TFT_SCLK = 12;

const char* WIFI_SSID = LOCAL_WIFI_SSID;
const char* WIFI_PASSWORD = LOCAL_WIFI_PASSWORD;

const char* MQTT_HOST = LOCAL_MQTT_HOST;
const int   MQTT_PORT = LOCAL_MQTT_PORT;
const char* MQTT_TOPIC = "suzu/display/status";

SPIClass* tftSPI = new SPIClass(FSPI);
Adafruit_ST7789 tft = Adafruit_ST7789(tftSPI, TFT_CS, TFT_DC, TFT_RST);

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String currentRole = "Live2D Agent";
String currentEmotion = "[idle]";
String currentStatus = "booting";
String currentMetric = "RAM --";
uint8_t customIconBits[128];
bool hasCustomIcon = false;
uint16_t customIconRgb565[1024];
bool hasColorIcon = false;

String fitAsciiText(const String& text, size_t maxLen) {
  if (text.length() <= maxLen) return text;
  if (maxLen <= 3) return text.substring(0, maxLen);
  return text.substring(0, maxLen - 3) + "...";
}

void drawTextBlock(int x, int y, uint16_t color, uint8_t size, const String& text, size_t maxLen) {
  tft.setTextColor(color, ST77XX_BLACK);
  tft.setTextSize(size);
  tft.setCursor(x, y);
  tft.print(fitAsciiText(text, maxLen));
}

String extractJsonString(const String& json, const char* key, const String& fallback) {
  String pattern = String("\"") + key + "\"";
  int keyPos = json.indexOf(pattern);
  if (keyPos < 0) return fallback;

  int colonPos = json.indexOf(':', keyPos + pattern.length());
  if (colonPos < 0) return fallback;

  int firstQuote = json.indexOf('"', colonPos + 1);
  if (firstQuote < 0) return fallback;

  int secondQuote = json.indexOf('"', firstQuote + 1);
  if (secondQuote < 0) return fallback;

  return json.substring(firstQuote + 1, secondQuote);
}

void drawFace(const String& emotion) {
  int screenW = tft.width();
  int screenH = tft.height();
  int cx = screenW / 4;
  int cy = screenH - 34;
  int r = min(screenW, screenH) / 10;
  if (r < 14) r = 14;
  if (r > 24) r = 24;

  tft.drawCircle(cx, cy, r, ST77XX_WHITE);
  tft.fillCircle(cx - 11, cy - 8, 3, ST77XX_WHITE);
  tft.fillCircle(cx + 11, cy - 8, 3, ST77XX_WHITE);

  if (emotion.indexOf("happy") >= 0 || emotion.indexOf("smile") >= 0) {
    tft.drawFastHLine(cx - 12, cy + 12, 24, ST77XX_GREEN);
    tft.drawPixel(cx - 13, cy + 11, ST77XX_GREEN);
    tft.drawPixel(cx + 12, cy + 11, ST77XX_GREEN);
  } else if (emotion.indexOf("sad") >= 0 || emotion.indexOf("cry") >= 0) {
    tft.drawFastHLine(cx - 10, cy + 16, 20, ST77XX_BLUE);
    tft.drawPixel(cx - 11, cy + 17, ST77XX_BLUE);
    tft.drawPixel(cx + 10, cy + 17, ST77XX_BLUE);
  } else if (emotion.indexOf("angry") >= 0) {
    tft.drawLine(cx - 18, cy - 16, cx - 6, cy - 11, ST77XX_RED);
    tft.drawLine(cx + 18, cy - 16, cx + 6, cy - 11, ST77XX_RED);
    tft.drawFastHLine(cx - 10, cy + 14, 20, ST77XX_RED);
  } else {
    tft.drawFastHLine(cx - 10, cy + 14, 20, ST77XX_WHITE);
  }
}

void drawCustomIcon(int originX, int originY) {
  if (hasColorIcon) {
    for (int y = 0; y < 32; y++) {
      for (int x = 0; x < 32; x++) {
        tft.drawPixel(originX + x, originY + y, customIconRgb565[y * 32 + x]);
      }
    }
    return;
  }
  if (!hasCustomIcon) return;
  for (int y = 0; y < 32; y++) {
    for (int x = 0; x < 32; x++) {
      int bitIndex = y * 32 + x;
      int byteIndex = bitIndex / 8;
      int bitOffset = 7 - (bitIndex % 8);
      bool on = (customIconBits[byteIndex] >> bitOffset) & 0x01;
      if (on) {
        tft.drawPixel(originX + x, originY + y, ST77XX_WHITE);
      }
    }
  }
}

void drawStatus() {
  int screenW = tft.width();
  int screenH = tft.height();
  int headerY = 18;
  int emoY = 70;
  int statusY = 112;
  int iconX = screenW - 58;
  int iconY = 68;
  int cardX = screenW / 2 + 2;
  int cardY = screenH - 62;
  int cardW = screenW - cardX - 10;
  int cardH = 52;
  if (cardW < 80) cardW = 80;

  tft.fillScreen(ST77XX_BLACK);
  tft.setTextWrap(false);

  drawTextBlock(12, headerY, ST77XX_WHITE, 2, currentRole, 16);

  tft.drawLine(10, 52, 230, 52, ST77XX_BLUE);

  drawTextBlock(12, emoY, ST77XX_YELLOW, 2, currentEmotion, 16);

  drawTextBlock(12, statusY, ST77XX_CYAN, 2, currentStatus, 18);

  tft.fillCircle(screenW - 18, 24, 10, WiFi.isConnected() ? ST77XX_GREEN : ST77XX_RED);

  tft.drawRoundRect(cardX, cardY, cardW, cardH, 8, ST77XX_WHITE);
  drawTextBlock(cardX + 12, cardY + 10, ST77XX_WHITE, 1, "SYSTEM", 10);
  drawTextBlock(cardX + 10, cardY + 28, ST77XX_YELLOW, 2, currentMetric, 8);

  if (hasCustomIcon || hasColorIcon) {
    drawCustomIcon(iconX, iconY);
  } else {
    drawFace(currentEmotion, iconX + 14, iconY + 16, 16);
  }
}

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.println("[WiFi] connecting...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] connected, ip=");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("[WiFi] connect failed");
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  Serial.print("[MQTT] message topic=");
  Serial.print(topic);
  Serial.print(" bytes=");
  Serial.println(length);

  String json;
  json.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) {
    json += static_cast<char>(payload[i]);
  }

  Serial.println(json);

  currentRole = extractJsonString(json, "role", currentRole);
  currentEmotion = extractJsonString(json, "emotion", currentEmotion);
  currentStatus = extractJsonString(json, "status", currentStatus);
  currentMetric = extractJsonString(json, "metric", currentMetric);

  String iconRgb565Hex = extractJsonString(json, "icon_rgb565", "");
  if (iconRgb565Hex.length() >= 4096) {
    for (int i = 0; i < 1024; i++) {
      String hexWord = iconRgb565Hex.substring(i * 4, i * 4 + 4);
      customIconRgb565[i] = static_cast<uint16_t>(strtol(hexWord.c_str(), nullptr, 16));
    }
    hasColorIcon = true;
  } else {
    hasColorIcon = false;
  }

  String iconBitsHex = extractJsonString(json, "icon_bits", "");
  if (iconBitsHex.length() >= 256) {
    for (int i = 0; i < 128; i++) {
      String hexByte = iconBitsHex.substring(i * 2, i * 2 + 2);
      customIconBits[i] = static_cast<uint8_t>(strtol(hexByte.c_str(), nullptr, 16));
    }
    hasCustomIcon = true;
  } else {
    hasCustomIcon = false;
  }

  drawStatus();
}

void ensureMqtt() {
  if (mqtt.connected()) return;
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(1024);

  String clientId = "esp32-status-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  Serial.print("[MQTT] connecting to ");
  Serial.print(MQTT_HOST);
  Serial.print(":");
  Serial.println(MQTT_PORT);
  if (mqtt.connect(clientId.c_str())) {
    mqtt.subscribe(MQTT_TOPIC);
    Serial.print("[MQTT] subscribed: ");
    Serial.println(MQTT_TOPIC);
    currentStatus = "mqtt connected";
    drawStatus();
  } else {
    Serial.print("[MQTT] connect failed, state=");
    Serial.println(mqtt.state());
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  tftSPI->begin(TFT_SCLK, -1, TFT_MOSI, -1);
  tft.init(240, 320);
  tft.setRotation(1);

  drawStatus();

  ensureWifi();
  if (WiFi.status() == WL_CONNECTED) {
    currentStatus = "wifi connected";
  } else {
    currentStatus = "wifi failed";
  }
  drawStatus();
}

void loop() {
  ensureWifi();
  if (WiFi.status() == WL_CONNECTED) {
    ensureMqtt();
    mqtt.loop();
  }
  delay(50);
}
