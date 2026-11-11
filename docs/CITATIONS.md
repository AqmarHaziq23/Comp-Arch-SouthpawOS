# Citations

The brief requires every source to be credited in the code **and** in the
video. Add a row whenever you use someone else's code, model, data or
tutorial, and put a credit comment at the top of the file that uses it.

| Used in | Source | Licence | Link | Added by |
|---------|--------|---------|------|----------|
| `hub/southpaw/ble.py` | bleak examples (`uart_service.py`, `two_devices.py`, `disconnect_callback.py`) | MIT | https://github.com/hbldh/bleak | Haziq |
| `hub/` (dependency) | bleak BLE library | MIT | https://github.com/hbldh/bleak | Haziq |
| `protocol/PROTOCOL.md` | Nordic UART Service UUIDs, as used by the TinyCircuits STBLE UART example | — | https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/ | Haziq |
| `firmware/wrist/wrist_logger/wrist_logger.ino` | TinyCircuits Accelerometer TinyShield example (`Accelerometer_TinyShield_example.ino`), code by Laverena Wienclaw for TinyCircuits | Not stated for code (repo README: design files CC BY-SA 3.0) | https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/ | Yue Ming |
| `firmware/wrist/` (dependency) | TinyCircuits BMA250 Arduino library | Not stated for code (repo README: design files CC BY-SA 3.0) | https://github.com/TinyCircuits/TinyCircuits-TinyShield-Sensor-ASD2511 | Yue Ming |
| `firmware/wrist/southpaw_wrist/southpaw_wrist.ino`, `sampler.*` | TinyCircuits Accelerometer TinyShield example (`Accelerometer_TinyShield_example.ino`), code by Laverena Wienclaw for TinyCircuits | Not stated for code (repo README: design files CC BY-SA 3.0) | https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/ | Yue Ming |
| `firmware/wrist/southpaw_wrist/ble.*` | TinyCircuits ST BLE TinyShield `UARTPassThrough` example (`UART.ino`), Ben Rose, TinyCircuits | Not stated (STBLE.zip has no licence file) | https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/ | Yue Ming |
| `firmware/wrist/` (dependency) | TinyCircuits STBLE library 0.1.0 (STBLE.zip), a port of STMicroelectronics' BlueNRG-MS stack (X-CUBE-BLE1) | Not stated by TinyCircuits; ST source files carry BSD-3-Clause or ST "guidance only" notices | https://github.com/TinyCircuits/TinyCircuits-TinyShield-BLE-ASD2116/raw/master/examples/STBLE.zip | Yue Ming |
| `firmware/wrist/southpaw_wrist/display.*` | TinyCircuits TinyScreen library and `TinyScreenBasicExample`, Ben Rose, TinyCircuits | LGPL (header: 2.1 or later; licence file: v3) | https://github.com/TinyCircuits/TinyCircuits-TinyScreen_Lib | Yue Ming |
| `firmware/wrist/southpaw_wrist/battery.*` | TinyCircuits TinyScreen/ST BLE Smartwatch example (`displayBattery()` in `display.ino`), Ben Rose, TinyCircuits; from `TinyScreen_Examples.zip` on the ICT1011 course site (xSiTe) | Not stated | ICT1011 course site (xSiTe), Content | Yue Ming |
| `training/tools/ble_listen.py` | bleak example `uart_service.py` | MIT | https://github.com/hbldh/bleak | Yue Ming |
| `training/tools/` (dependency) | bleak BLE library | MIT | https://github.com/hbldh/bleak | Yue Ming |
