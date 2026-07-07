#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>

// 7针 SPI TFT 接线
static const int TFT_CS   = 8;
static const int TFT_DC   = 9;
static const int TFT_RST  = 10;
static const int TFT_MOSI = 11;
static const int TFT_SCLK = 12;

// 1. 显式创建一个硬件 SPI 实例 (使用 ESP32 的 HSPI / FSPI)
SPIClass *tftSPI = new SPIClass(FSPI);

// 2. 调用 Adafruit 的高阶构造函数，强行注入我们自定义的 SPI 指针
Adafruit_ST7789 tft = Adafruit_ST7789(tftSPI, TFT_CS, TFT_DC, TFT_RST);

void drawStatus(const char *title, const char *emotion, const char *status) {
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextWrap(false);

  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.setCursor(12, 20);
  tft.println(title);

  tft.drawLine(10, 52, 230, 52, ST77XX_BLUE);

  tft.setTextColor(ST77XX_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(12, 75);
  tft.println(emotion);

  tft.setTextColor(ST77XX_CYAN);
  tft.setTextSize(2);
  tft.setCursor(12, 120);
  tft.println(status);

  tft.fillCircle(200, 30, 10, ST77XX_GREEN);
}

void drawBars() {
  tft.fillRect(10, 190, 40, 30, ST77XX_RED);
  tft.fillRect(55, 190, 40, 30, ST77XX_GREEN);
  tft.fillRect(100, 190, 40, 30, ST77XX_BLUE);
  tft.fillRect(145, 190, 40, 30, ST77XX_YELLOW);
  tft.fillRect(190, 190, 40, 30, ST77XX_MAGENTA);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("ESP32-S3 TFT test booting...");

  // 3. 在底层初始化我们自定义的 SPI 总线
  // 注意：SS 引脚传 -1，将 CS 的控制权完全交给 Adafruit 库
  tftSPI->begin(TFT_SCLK, -1, TFT_MOSI, -1);

  // 4. 屏幕初始化
  tft.init(240, 320);
  tft.setRotation(1); // 根据需要调整屏幕方向 (0-3)

  drawStatus("Live2D Agent", "[calm]", "screen ready");
  drawBars();

  Serial.println("TFT init done.");
}

void loop() {
  static unsigned long last = 0;
  static bool toggle = false;
  if (millis() - last > 3000) {
    last = millis();
    toggle = !toggle;
    if (toggle) {
      drawStatus("Toyokawa", "[happy]", "welcome back");
    } else {
      drawStatus("Suzu", "[idle]", "waiting input");
    }
    drawBars();
    Serial.println(toggle ? "State A" : "State B");
  }
}