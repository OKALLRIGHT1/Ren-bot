#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <SPI.h>

static const int TFT_CS = 8;
static const int TFT_DC = 9;
static const int TFT_RST = 10;
static const int TFT_MOSI = 11;
static const int TFT_SCLK = 12;

Adafruit_ILI9341 tft = Adafruit_ILI9341(TFT_CS, TFT_DC, TFT_RST);

void drawScreen(const char *title, const char *status) {
  tft.fillScreen(ILI9341_BLACK);
  tft.setTextWrap(false);
  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(2);
  tft.setCursor(12, 20);
  tft.println(title);
  tft.drawLine(10, 52, 230, 52, ILI9341_CYAN);
  tft.setCursor(12, 80);
  tft.setTextColor(ILI9341_YELLOW);
  tft.println(status);
  tft.fillRect(10, 180, 40, 30, ILI9341_RED);
  tft.fillRect(60, 180, 40, 30, ILI9341_GREEN);
  tft.fillRect(110, 180, 40, 30, ILI9341_BLUE);
  tft.fillRect(160, 180, 40, 30, ILI9341_WHITE);
}

void setup() {
  Serial.begin(115200);
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);
  tft.init(240, 320);   // 这里后续改尺寸测试
  tft.setRotation(1);

  tft.fillScreen(ST77XX_RED);
  delay(1000);
  tft.fillScreen(ST77XX_GREEN);
  delay(1000);
  tft.fillScreen(ST77XX_BLUE);
  delay(1000);
  tft.fillScreen(ST77XX_WHITE);
}

void loop() {
}
