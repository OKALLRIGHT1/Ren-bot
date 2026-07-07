#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <U8g2_for_Adafruit_GFX.h>
#include "local_secrets.h"

static const int TFT_CS = 8;
static const int TFT_DC = 9;
static const int TFT_RST = 10;
static const int TFT_MOSI = 11;
static const int TFT_SCLK = 12;

const char* WIFI_SSID = LOCAL_WIFI_SSID;
const char* WIFI_PASSWORD = LOCAL_WIFI_PASSWORD;
const char* MQTT_HOST = LOCAL_MQTT_HOST;
const int MQTT_PORT = LOCAL_MQTT_PORT;
const char* MQTT_TOPIC = "suzu/display/status";

SPIClass* tftSPI = new SPIClass(FSPI);
Adafruit_ST7789 tft = Adafruit_ST7789(tftSPI, TFT_CS, TFT_DC, TFT_RST);
U8G2_FOR_ADAFRUIT_GFX u8g2Fonts;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

String currentRole = "Live2D Agent";
String currentEmotion = "[idle]";
String currentStatus = "booting";
String currentMetric = "RAM --";
uint16_t customIconRgb565[1024];
bool hasColorIcon = false;
uint8_t customIconBits[128];
bool hasMonoIcon = false;

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

void drawUtf8Text(int x, int y, uint16_t color, const String& text) {
  u8g2Fonts.setFontMode(1);
  u8g2Fonts.setForegroundColor(color);
  u8g2Fonts.setCursor(x, y);
  u8g2Fonts.print(text);
}

void drawWrappedUtf8(int x, int y, uint16_t color, const String& text, int maxCharsPerLine, int lineHeight, int maxLines) {
  u8g2Fonts.setFontMode(1);
  u8g2Fonts.setForegroundColor(color);
  
  int bytes = text.length();
  int i = 0;
  int line = 0;

  while (i < bytes && line < maxLines) {
    int chars = 0;
    int start = i;
    while (i < bytes && chars < maxCharsPerLine) {
      uint8_t c = text[i];
      if ((c & 0x80) == 0) i += 1;
      else if ((c & 0xE0) == 0xC0) i += 2;
      else if ((c & 0xF0) == 0xE0) i += 3;
      else if ((c & 0xF8) == 0xF0) i += 4;
      else i += 1;
      chars++;
    }
    
    String part = text.substring(start, i);
    
    if (line == maxLines - 1 && i < bytes) {
      part += "...";
    }
    
    u8g2Fonts.setCursor(x, y + line * lineHeight);
    u8g2Fonts.print(part);
    line++;
  }
}

void drawCustomIcon(int originX, int originY, int scale) {
  if (hasColorIcon) {
    for (int y = 0; y < 32; y++) {
      for (int x = 0; x < 32; x++) {
        uint16_t color = customIconRgb565[y * 32 + x];
        if (scale == 1) {
          tft.drawPixel(originX + x, originY + y, color);
        } else {
          tft.fillRect(originX + x * scale, originY + y * scale, scale, scale, color);
        }
      }
    }
    return;
  }
  if (!hasMonoIcon) return;
  for (int y = 0; y < 32; y++) {
    for (int x = 0; x < 32; x++) {
      int bitIndex = y * 32 + x;
      int byteIndex = bitIndex / 8;
      int bitOffset = 7 - (bitIndex % 8);
      bool on = (customIconBits[byteIndex] >> bitOffset) & 0x01;
      if (on) {
        if (scale == 1) {
          tft.drawPixel(originX + x, originY + y, ST77XX_WHITE);
        } else {
          tft.fillRect(originX + x * scale, originY + y * scale, scale, scale, ST77XX_WHITE);
        }
      }
    }
  }
}

void drawFace(const String& emotion, int x, int y, int r) {
  tft.drawCircle(x, y, r, ST77XX_WHITE);
  tft.fillCircle(x - (r*0.4), y - (r*0.3), r*0.1, ST77XX_WHITE);
  tft.fillCircle(x + (r*0.4), y - (r*0.3), r*0.1, ST77XX_WHITE);
  if (emotion.indexOf("happy") >= 0 || emotion.indexOf("smile") >= 0) {
    tft.drawFastHLine(x - (r*0.45), y + (r*0.4), r*0.9, ST77XX_GREEN);
  } else if (emotion.indexOf("sad") >= 0 || emotion.indexOf("cry") >= 0) {
    tft.drawFastHLine(x - (r*0.4), y + (r*0.55), r*0.8, ST77XX_BLUE);
  } else if (emotion.indexOf("angry") >= 0) {
    tft.drawLine(x - (r*0.6), y - (r*0.55), x - (r*0.2), y - (r*0.4), ST77XX_RED);
    tft.drawLine(x + (r*0.6), y - (r*0.55), x + (r*0.2), y - (r*0.4), ST77XX_RED);
    tft.drawFastHLine(x - (r*0.4), y + (r*0.4), r*0.8, ST77XX_RED);
  } else {
    tft.drawFastHLine(x - (r*0.4), y + (r*0.4), r*0.8, ST77XX_WHITE);
  }
}

void drawStatus() {
  int screenW = tft.width();  // 320
  int screenH = tft.height(); // 240

  tft.fillScreen(ST77XX_BLACK);
  
  tft.fillCircle(screenW - 18, 22, 9, WiFi.isConnected() ? ST77XX_GREEN : ST77XX_RED);

  int leftMargin = 12;
  int lineHeight = 26; 
  
  // === 角色信息 ===
  drawUtf8Text(leftMargin, 34, ST77XX_WHITE, "Role:");
  drawWrappedUtf8(leftMargin + 48, 34, ST77XX_CYAN, currentRole, 9, lineHeight, 2); 
  
  // === 状态信息 (包含视觉锚点容器) ===
  drawUtf8Text(leftMargin, 74, ST77XX_WHITE, "Status:");
  tft.fillRoundRect(leftMargin, 86, 168, 92, 4, tft.color565(119, 187, 221)); 
  // 【安全修正】：已将 15 强制降回 10。详见下方分析。
  drawWrappedUtf8(leftMargin + 6, 110, ST77XX_YELLOW, currentStatus, 10, lineHeight, 4);
  
  // === 情绪参数 (Mood已移动至此) ===
  drawUtf8Text(leftMargin, 216, ST77XX_WHITE, "Mood:");
  // 英文单字符宽约8px，剩余可用空间 130px，130 / 8 ≈ 16。故最高限制为 16。
  drawWrappedUtf8(leftMargin + 48, 216, ST77XX_YELLOW, currentEmotion, 16, lineHeight, 1);

  // === 右侧视觉列 ===
  int rightX = 190;
  int rightWidth = screenW - rightX; 
  int iconScale = 3; 
  int iconRealSize = 32 * iconScale; 
  int iconX = rightX + (rightWidth - iconRealSize) / 2;
  int iconY = 24;

  if (hasColorIcon || hasMonoIcon) {
    drawCustomIcon(iconX, iconY, iconScale);
  } else {
    drawFace(currentEmotion, rightX + (rightWidth / 2), iconY + 48, 40);
  }

  // === 系统参数卡片 (Sys已移动至此) ===
  int cardX = rightX + 4;
  int cardY = 156;
  int cardW = rightWidth - 12;
  int cardH = 68;
  
  tft.drawRoundRect(cardX, cardY, cardW, cardH, 8, ST77XX_WHITE);
  drawUtf8Text(cardX + 12, cardY + 26, ST77XX_WHITE, "Sys");
  // 参数多为短字符 "RAM XX%"，卡片内宽约 118px，118 / 8 ≈ 14，限制为 12 极度安全。
  drawWrappedUtf8(cardX + 12, cardY + 52, ST77XX_GREEN, currentMetric, 12, 20, 1);
}

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300);
  }
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  Serial.print("[MQTT] topic=");
  Serial.print(topic);
  Serial.print(" bytes=");
  Serial.println(length);
  String json;
  json.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) json += static_cast<char>(payload[i]);

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
    hasMonoIcon = true;
  } else {
    hasMonoIcon = false;
  }

  drawStatus();
}

void ensureMqtt() {
  if (mqtt.connected()) return;
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(8192);
  String clientId = "esp32-status-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(clientId.c_str())) {
    mqtt.subscribe(MQTT_TOPIC);
    currentStatus = "mqtt connected";
    drawStatus();
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  tftSPI->begin(TFT_SCLK, -1, TFT_MOSI, -1);
  tft.init(240, 320);
  tft.setRotation(1);
  tft.invertDisplay(false); 
  tft.setSPISpeed(40000000); 

  u8g2Fonts.begin(tft);
  u8g2Fonts.setFontMode(1);
  u8g2Fonts.setFontDirection(0);
  u8g2Fonts.setForegroundColor(ST77XX_WHITE);
  u8g2Fonts.setFont(u8g2_font_wqy16_t_gb2312a); 

  drawStatus();
  ensureWifi();
  currentStatus = (WiFi.status() == WL_CONNECTED) ? "wifi connected" : "wifi failed";
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
