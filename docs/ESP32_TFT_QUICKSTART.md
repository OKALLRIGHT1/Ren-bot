# ESP32-S3 + 7针 SPI TFT 快速开始

当前状态：这是硬件快速上手文档，适合先点亮屏幕和验证 MQTT 状态显示。主脑、知识库、TTS、QQ 和 Live2D 仍建议继续跑在主电脑上。

适用对象：

- `ESP32-S3 N16R8（已焊排针）`
- 7 针 SPI TFT 屏，针脚顺序：`CS DC RST SDA SCK VCC GND`

---

## 1. 接线建议

按下面接线先做最小点亮测试：

```text
屏幕 CS   -> ESP32 GPIO 8
屏幕 DC   -> ESP32 GPIO 9
屏幕 RST  -> ESP32 GPIO 10
屏幕 SDA  -> ESP32 GPIO 11
屏幕 SCK  -> ESP32 GPIO 12
屏幕 VCC  -> ESP32 3V3
屏幕 GND  -> ESP32 GND
```

说明：

- `SDA` 在 SPI 屏里一般等同于 `MOSI`
- 当前先不接按钮和 ToF，先把屏点亮
- 开发阶段直接用电脑 USB 给 ESP32 供电即可
- `VCC` 和 `GND` 不一定非要走面包板电源轨，直接飞线接到 ESP32 的 `3V3` / `GND` 也完全可以
- 只有当你后面还要继续接 ToF / 按钮 / PIR 时，才更建议先把 `3V3` / `GND` 分到面包板电源轨上

---

## 2. ESP32 两个 USB 口怎么选

优先用标着 `USB` 的口：

- 用于供电
- 用于烧录
- 用于串口日志

如果电脑识别不到串口，再试另一个口。

---

## 3. Arduino IDE 配置

1. 安装 `Arduino IDE`
2. 在开发板管理器安装：`ESP32 by Espressif Systems`
3. 开发板先选：`ESP32S3 Dev Module`
4. 安装库：
   - `Adafruit GFX Library`
   - `Adafruit ST7735 and ST7789 Library`
   - `PubSubClient`
   - `U8g2_for_Adafruit_GFX`

如果准备第二阶段直接做 Wi‑Fi + MQTT 状态同步，还需要：

- 中文字库会通过 `U8g2_for_Adafruit_GFX` 处理，避免 TFT 默认英文字库导致中文乱码

---

## 4. 测试程序

示例文件：

- `hardware/esp32_s3_tft_status/esp32_s3_tft_status.ino`
- `hardware/esp32_s3_tft_ili9341/esp32_s3_tft_ili9341.ino`
- `hardware/esp32_s3_tft_st7735/esp32_s3_tft_st7735.ino`
- `hardware/esp32_s3_tft_status_wifi_utf8/esp32_s3_tft_status_wifi_utf8.ino`

先烧录它，确认屏幕能显示文字和彩条。

如果出现：

- 白屏
- 花屏
- 颜色不对

则大概率是屏幕控制器不是 `ST7789`，而是别的型号（如 `ILI9341` / `ST7735`）。这时再根据实际屏幕型号调整驱动库。

### 白屏排查顺序

1. 先确认接线：
   - `VCC -> 3V3`
   - `GND -> GND`
   - `SDA -> GPIO 11`
   - `SCK -> GPIO 12`
   - `CS -> GPIO 8`
   - `DC -> GPIO 9`
   - `RST -> GPIO 10`
2. 保持当前接线不变，依次烧录：
- `esp32_s3_tft_status.ino`（ST7789）
- `esp32_s3_tft_ili9341.ino`
- `esp32_s3_tft_st7735.ino`
   - 注意：这三个程序必须分别放在不同文件夹里打开；Arduino 会编译当前 sketch 文件夹下所有 `.ino`，不能把三个测试文件放在同一个目录里一起编译
3. 如果还是白屏，再尝试把 `VCC` 改接 `5V`（前提是你的屏幕模块明确支持 5V 输入）
4. 如果屏幕有额外背光焊盘或 `BL/LED` 引脚，那还需要额外给背光供电；你这个 7 针版本通常不需要

### 如何判断是哪种情况

- `黑屏`：更像供电/背光问题
- `纯白屏`：更像驱动型号不对，或初始化没成功
- `花屏/错位`：更像分辨率、驱动型号、rotation 不对

---

## 5. 第一阶段建议顺序

1. 点亮 TFT 屏
2. 串口打印正常
3. 再接按钮
4. 再接 ToF
5. 最后做 MQTT 通信

不要一开始就把所有模块都接上，这样最容易排错。

---

## 6. 第二阶段：让板子连 Wi‑Fi 并接收主电脑状态

等你确认屏幕已经点亮后，再进入这一阶段。

建议做法：

1. 先让 ESP32 连上 Wi‑Fi
2. 再让 ESP32 连接 MQTT broker
3. 订阅一个状态主题，例如：

```text
suzu/display/status
```

4. 当主电脑发布新状态时，ESP32 刷新屏幕显示

推荐显示内容：

- 当前角色名
- 当前情绪
- 一句状态文字
- 右侧系统指标（例如内存占用、CPU 提示）

例如：

```json
{
  "role": "丰川祥子",
  "emotion": "calm",
  "status": "welcome_back",
  "metric": "RAM 42%"
}
```

如果你准备进入第二阶段，我建议下一步直接换用支持 Wi‑Fi + MQTT 的状态屏程序。

### 如果主程序显示“推送成功”但板子没反应

最常见原因是：

- ESP32 端 MQTT 接收缓冲区太小
- MQTT 消息体比默认缓冲区大，被直接丢弃

当前示例程序已经把：

- `mqtt.setBufferSize(1024)`

加上了，并且增加了串口日志。重新烧录后，打开串口监视器可以看到：

- Wi‑Fi 是否连上
- MQTT 是否成功连接
- 是否真的收到主题消息

### 主程序自动推送状态到屏幕

现在项目里也支持主程序自动把状态推到 MQTT 状态屏。

在 `.env` 或运行环境里配置：

```text
MQTT_DISPLAY_ENABLED=1
MQTT_DISPLAY_HOST=192.168.50.60
MQTT_DISPLAY_PORT=1883
MQTT_DISPLAY_TOPIC=suzu/display/status
```

前提：

- 电脑上已有可被局域网访问的 Mosquitto
- 主程序能连上该 broker

如果状态屏管理里显示 `MQTT 未连接`，最常见原因是主程序缺少 Python 依赖：

```powershell
pip install paho-mqtt
```

之后桌面角色状态变化时，主程序会自动推送：

- 当前角色名
- 当前真实情绪
- 当前状态
- 当前内存占用（metric）

### Mosquitto 开机自动启动

如果你不想每次手动运行：

```powershell
cd D:\Mosquitto
.\mosquitto.exe -c .\mosquitto_local.conf -v
```

可以使用仓库里提供的脚本（需要管理员权限）：

- `tools/mosquitto/install_mosquitto_service.bat`
- `tools/mosquitto/uninstall_mosquitto_service.bat`

安装脚本会把 Mosquitto 注册成 Windows 服务，并让它使用：

- `D:\Mosquitto\mosquitto_local.conf`

然后开机自启。

如果服务方式仍然不稳定，你也可以改用“无窗口后台启动”方式：

- `tools/mosquitto/start_mosquitto_background.vbs`

双击这个文件后，Mosquitto 会在后台启动，不弹出命令行窗口。

如果你想确认它是否已启动，可以运行：

- `tools/mosquitto/check_mosquitto_status.bat`

如果你想远程自定义状态屏显示，现在还支持额外字段：

- `metric`：右下指标卡片文字，可用来显示 `RAM 42%`、`CPU 18%`、`天气 晴 26C` 等
- `icon_bits`：自定义 32x32 1-bit 图标数据（十六进制字符串），会替换左下默认小表情
- `icon_rgb565`：自定义 32x32 彩色 RGB565 图标数据（优先级高于黑白位图）
- `icon_w` / `icon_h`：当前图标尺寸（预留）

推荐做法：

- 默认让主程序自动推送角色/状态/内存占用
- 在 GUI 里手动覆盖测试自定义图标和指标文字

### 默认图与情绪差分规则

现在主程序支持保存状态屏默认规则：

- `默认图标`
- `情绪差分图标`
- `指标模式`

推荐规则：

1. 对每个常见情绪单独配置差分图（如 `happy/sad/angry/think`）
2. 没有配置差分图的情绪，自动回退到默认图
3. 如果默认图也没设置，ESP32 再回退到内置小脸

当前 GUI 里上传图片后，会同时生成：

- 黑白位图
- 彩色 RGB565 图标

ESP32 会优先使用彩色图标显示。

### 指标模式

当前支持三种：

- `auto_ram`：默认显示内存占用
- `status_priority`：说话/思考/监听优先覆盖指标区
- `custom`：固定显示你在 GUI 里填的文字，例如天气

这些规则可以在主程序 GUI 的 `状态屏管理` 中保存。

### 中文乱码说明

如果 MQTT 通信已经正常，但像 `丰川祥子` 这样的中文显示成乱码，通常不是 MQTT 问题，而是：

- TFT 默认字体只支持 ASCII / 英文

当前提供的 `esp32_s3_tft_status_wifi.ino` 已经切换为：

- `U8g2_for_Adafruit_GFX`
- `u8g2_font_wqy12_t_gb2312a`

支持常见简体中文显示。

如果你希望：

- 中文正常显示
- 表情放在情绪文字右侧
- 系统指标显示在下方卡片里

建议直接改用：

- `hardware/esp32_s3_tft_status_wifi_utf8/esp32_s3_tft_status_wifi_utf8.ino`

这个版本会：

- 使用中文字库绘制中文
- 使用左右布局：左侧约 3/5 显示角色 / 情绪 / 状态，右侧显示差分表情和系统卡片
- 更适合长期作为状态屏使用

同时主程序现在会把：

- 角色名
- 情绪
- 状态
- 指标

预先渲染成位图再发给状态屏，所以中文显示会更稳定，不再完全依赖板子端字体渲染效果。

如果编译这个 UTF8 版本时只看到：

```text
exit status 1
Compilation error: exit status 1
```

大概率是你的 Arduino 环境没有正确安装 `U8g2_for_Adafruit_GFX` 相关库。

这时有两种选择：

1. 先继续使用不带中文字库的版本：
   - `hardware/esp32_s3_tft_status_wifi/esp32_s3_tft_status_wifi.ino`
2. 后面补装完整 U8g2 相关库后，再切换到 UTF8 版本

注意：如果没有完整报错内容，只显示 `exit status 1`，请在 Arduino IDE 底部展开“详细输出”或把完整编译日志复制出来，才能继续准确排查。

### 当前状态屏布局

现在这版状态屏支持：

- 上方：角色名
- 中间：情绪 / 状态文字
- 左下：简单情绪小表情
- 右下：系统指标卡片（如 `RAM 42%`）

如果你发现：

- 小表情只显示一部分
- 右下系统卡片被切掉

说明当前屏幕的实际可视区域比预设更小。当前示例程序已经改成根据 `tft.width()` / `tft.height()` 做自适应布局，能更好适配不同尺寸和可视区的 ST7789 屏幕。

你后面可以继续扩展 MQTT 消息里的字段，做更多自定义显示。
