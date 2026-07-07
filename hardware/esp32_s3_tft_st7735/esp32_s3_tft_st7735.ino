#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>

static const int TFT_CS = 8;
static const int TFT_DC = 9;
static const int TFT_RST = 10;
static const int TFT_MOSI = 11;
static const int TFT_SCLK = 12;

Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST);

void drawScreen(const char *title, const char *status) {
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextWrap(false);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(1);
  tft.setCursor(8, 12);
  tft.println(title);
  tft.drawLine(5, 28, 155, 28, ST77XX_CYAN);
  tft.setCursor(8, 42);
  tft.setTextColor(ST77XX_YELLOW);
  tft.println(status);
  tft.fillRect(8, 80, 20, 16, ST77XX_RED);
  tft.fillRect(32, 80, 20, 16, ST77XX_GREEN);
  tft.fillRect(56, 80, 20, 16, ST77XX_BLUE);
  tft.fillRect(80, 80, 20, 16, ST77XX_WHITE);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("ESP32-S3 ST7735 test booting...");
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(1);
  drawScreen("ST7735 Test", "screen ready");
}

void loop() {
}
