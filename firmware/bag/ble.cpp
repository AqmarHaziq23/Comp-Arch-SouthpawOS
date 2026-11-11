/*
 * Based on: TinyCircuits ST BLE TinyShield UARTPassThrough example (UART.ino) by Ben Rose,
 *   TinyCircuits, STBLE library, https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) – advertises as BLE_NAME with a per-hand address, answers the
 *   protocol's T clock ping, queues outgoing protocol lines, no blocking waits or debug sprintf.
 * Adapted for the bag board by Haziq (Team P1D) – named P1D-BAG, and hands the Pi's other
 *   commands (C, S, M) to the sketch through ble_take_line().
 */
#include <Arduino.h>
#include <SPI.h>
#include <STBLE.h>
#include <string.h>

// The bag board's name and address (the wrists use ...:4C and ...:52).
#define BLE_NAME "P1D-BAG"
// Written least-significant byte first: reads 02:80:E1:50:31:42.
#define BLE_PUBLIC_ADDR {0x42, 0x31, 0x50, 0xE1, 0x80, 0x02}
#include "ble.h"

constexpr uint16_t ADV_INTERVAL_MIN_MS = 50;
constexpr uint16_t ADV_INTERVAL_MAX_MS = 100;
constexpr uint8_t TX_QUEUE = 4;
constexpr uint8_t TX_MAX_TRIES = 3;

static bool started = false;
static bool connected = false;          // link is up
static bool notifyOn = false;           // ...and the Pi has switched on notifications, so sends arrive
static bool needAdvertise = true;
static uint16_t connHandle = 0;
static uint16_t uartServHandle = 0;
static uint16_t uartWriteHandle = 0;    // 6E400002: Pi -> board
static uint16_t uartNotifyHandle = 0;   // 6E400003: board -> Pi
static uint8_t advAddr[6] = {0};        // least-significant byte first

static char rxLine[BLE_MAX_MSG + 1];
static uint8_t rxLen = 0;
static bool pingPending = false;
static uint32_t pingMs = 0;

// Commands from the Pi waiting for the sketch (C, S, M lines, without '\n').
constexpr uint8_t RX_QUEUE = 4;
static char rxq[RX_QUEUE][BLE_MAX_MSG + 1];
static uint8_t rxHead = 0, rxCount = 0;

static char txq[TX_QUEUE][BLE_MAX_MSG];
static uint8_t txLen[TX_QUEUE];
static uint8_t txHead = 0, txCount = 0, txTries = 0;

static BleStats stats = {0, 0, 0, 0, 0, 0, 0};

// ---- GATT: Nordic UART Service (same UUIDs and properties as the TinyCircuits example) ----
#define COPY_UUID_128(u, b15, b14, b13, b12, b11, b10, b9, b8, b7, b6, b5, b4, b3, b2, b1, b0) \
  do { u[0] = b0; u[1] = b1; u[2] = b2; u[3] = b3; u[4] = b4; u[5] = b5; u[6] = b6; u[7] = b7; \
       u[8] = b8; u[9] = b9; u[10] = b10; u[11] = b11; u[12] = b12; u[13] = b13; u[14] = b14; u[15] = b15; } while (0)
#define NUS_UUID(u, n) COPY_UUID_128(u, 0x6E, 0x40, 0x00, n, 0xB5, 0xA3, 0xF3, 0x93, 0xE0, 0xA9, 0xE5, 0x0E, 0x24, 0xDC, 0xCA, 0x9E)

static tBleStatus addUartService() {
  uint8_t uuid[16];
  NUS_UUID(uuid, 0x01);
  tBleStatus ret = aci_gatt_add_serv(UUID_TYPE_128, uuid, PRIMARY_SERVICE, 7, &uartServHandle);
  if (ret != BLE_STATUS_SUCCESS) return ret;
  NUS_UUID(uuid, 0x02);
  ret = aci_gatt_add_char(uartServHandle, UUID_TYPE_128, uuid, BLE_MAX_MSG, CHAR_PROP_WRITE_WITHOUT_RESP,
                          ATTR_PERMISSION_NONE, GATT_NOTIFY_ATTRIBUTE_WRITE, 16, 1, &uartWriteHandle);
  if (ret != BLE_STATUS_SUCCESS) return ret;
  NUS_UUID(uuid, 0x03);
  return aci_gatt_add_char(uartServHandle, UUID_TYPE_128, uuid, BLE_MAX_MSG, CHAR_PROP_NOTIFY,
                           ATTR_PERMISSION_NONE, 0, 16, 1, &uartNotifyHandle);
}

static void startAdvertising() {
  static const char localName[] = {AD_TYPE_COMPLETE_LOCAL_NAME, 'P', '1', 'D', '-', 'B', 'A', 'G'};
  static_assert(sizeof(localName) == sizeof(BLE_NAME), "advertised name must match BLE_NAME");
  hci_le_set_scan_resp_data(0, NULL);
  aci_gap_set_discoverable(ADV_IND, (ADV_INTERVAL_MIN_MS * 1000) / 625, (ADV_INTERVAL_MAX_MS * 1000) / 625,
                           STATIC_RANDOM_ADDR, NO_WHITE_LIST_USE, sizeof(localName), localName, 0, NULL, 0, 0);
}

bool ble_begin() {
  HCI_Init();
  BNRG_SPI_Init();
  BlueNRG_RST();

  const uint8_t publicAddr[6] = BLE_PUBLIC_ADDR;
  if (aci_hal_write_config_data(CONFIG_DATA_PUBADDR_OFFSET, CONFIG_DATA_PUBADDR_LEN, publicAddr) != BLE_STATUS_SUCCESS) {
    return false;                         // no answer: shield missing or not seated
  }
  aci_gatt_init();
  uint16_t gapService, nameChar, appearanceChar;
  aci_gap_init_IDB05A1(GAP_PERIPHERAL_ROLE_IDB05A1, 0, sizeof(BLE_NAME) - 1, &gapService, &nameChar, &appearanceChar);
  aci_gatt_update_char_value(gapService, nameChar, 0, sizeof(BLE_NAME) - 1, (const uint8_t*)BLE_NAME);
  if (addUartService() != BLE_STATUS_SUCCESS) return false;
  aci_hal_set_tx_power_level(1, 3);       // +4 dBm, as in the example

  uint8_t outLen = 0;
  aci_hal_read_config_data(CONFIG_DATA_RANDOM_ADDRESS, 6, &outLen, advAddr);
  started = true;
  needAdvertise = true;
  return true;
}

static bool sendNow(const char* data, uint8_t len) {
  uint32_t t0 = micros();
  tBleStatus ret = aci_gatt_update_char_value(uartServHandle, uartNotifyHandle, 0, len, (const uint8_t*)data);
  uint32_t d = micros() - t0;
  if (d > stats.maxSendUs) stats.maxSendUs = d;
  if (ret != BLE_STATUS_SUCCESS) stats.failed++;
  return ret == BLE_STATUS_SUCCESS;
}

static void answerPing() {
  // "T,<millis when the ping arrived>\n"
  char msg[BLE_MAX_MSG];
  char digits[10];
  uint8_t n = 0, len = 0;
  uint32_t v = pingMs;
  do { digits[n++] = (char)('0' + v % 10); v /= 10; } while (v);
  msg[len++] = 'T';
  msg[len++] = ',';
  while (n) msg[len++] = digits[--n];
  msg[len++] = '\n';
  if (sendNow(msg, len)) stats.pings++;
}

void ble_poll() {
  if (!started) return;
  uint32_t t0 = micros();

  HCI_Process();                          // runs HCI_Event_CB for anything the BlueNRG sent
  if (needAdvertise) {
    needAdvertise = false;
    startAdvertising();
  }
  if (pingPending) {                      // the clock reply goes first, before any other work
    pingPending = false;
    if (connected) answerPing();
  } else if (txCount) {
    if (!connected || !notifyOn) {
      stats.dropped += txCount;
      txCount = 0;
      txTries = 0;
    } else if (sendNow(txq[txHead], txLen[txHead])) {
      stats.sent++;
      txHead = (uint8_t)((txHead + 1) % TX_QUEUE);
      txCount--;
      txTries = 0;
    } else if (++txTries >= TX_MAX_TRIES) {
      stats.dropped++;
      txHead = (uint8_t)((txHead + 1) % TX_QUEUE);
      txCount--;
      txTries = 0;
    }
  }

  uint32_t d = micros() - t0;
  if (d > stats.maxPollUs) stats.maxPollUs = d;
}

// "Connected" means messages will arrive: the link is up AND notifications are on.
// Punches sent between the two (while the Pi is still discovering services) were lost.
bool ble_connected() { return connected && notifyOn; }
bool ble_link_up() { return connected; }

bool ble_queue_line(const char* line, uint8_t len) {
  if (!started || !connected || !notifyOn || len == 0 || len > BLE_MAX_MSG || txCount >= TX_QUEUE) {
    stats.dropped++;
    return false;
  }
  uint8_t slot = (uint8_t)((txHead + txCount) % TX_QUEUE);
  memcpy(txq[slot], line, len);
  txLen[slot] = len;
  txCount++;
  return true;
}

void ble_address(char out[18]) {
  static const char HEX_DIGITS[] = "0123456789ABCDEF";
  uint8_t p = 0;
  for (int8_t i = 5; i >= 0; i--) {
    out[p++] = HEX_DIGITS[advAddr[i] >> 4];
    out[p++] = HEX_DIGITS[advAddr[i] & 0x0F];
    if (i) out[p++] = ':';
  }
  out[p] = '\0';
}

void ble_take_stats(BleStats& out) {
  out = stats;
  stats.maxPollUs = stats.maxSendUs = 0;
  stats.sent = stats.pings = stats.failed = stats.dropped = stats.ignoredRx = 0;
}

bool ble_take_line(char* out) {
  if (!rxCount) return false;
  memcpy(out, rxq[rxHead], BLE_MAX_MSG + 1);
  rxHead = (uint8_t)((rxHead + 1) % RX_QUEUE);
  rxCount--;
  return true;
}

// ---- Received bytes: the Pi sends one line per write; a write also ends a line ----
static void endRxLine() {
  if (rxLen == 1 && rxLine[0] == 'T') {
    pingPending = true;
  } else if (rxLen > 0) {
    if (rxCount < RX_QUEUE) {             // hand C / S / M lines to the sketch
      uint8_t slot = (uint8_t)((rxHead + rxCount) % RX_QUEUE);
      memcpy(rxq[slot], rxLine, rxLen);
      rxq[slot][rxLen] = '\0';
      rxCount++;
    } else {
      stats.ignoredRx++;                  // sketch isn't collecting them fast enough
    }
  }
  rxLen = 0;
}

static void onWrite(uint8_t len, const uint8_t* data) {
  pingMs = millis();                      // "board time when the ping arrived"
  for (uint8_t i = 0; i < len; i++) {
    char c = (char)data[i];
    if (c == '\n' || c == '\r') {
      endRxLine();
    } else if (rxLen < BLE_MAX_MSG) {
      rxLine[rxLen++] = c;
    }
  }
  endRxLine();
}

// Called by the STBLE library from HCI_Process(), never from an interrupt.
void HCI_Event_CB(void* pckt) {
  hci_uart_pckt* hciPckt = (hci_uart_pckt*)pckt;
  hci_event_pckt* eventPckt = (hci_event_pckt*)hciPckt->data;
  if (hciPckt->type != HCI_EVENT_PKT) return;

  switch (eventPckt->evt) {
    case EVT_DISCONN_COMPLETE:
      connected = false;
      notifyOn = false;
      connHandle = 0;
      needAdvertise = true;               // become connectable again
      break;

    case EVT_LE_META_EVENT: {
      evt_le_meta_event* evt = (evt_le_meta_event*)eventPckt->data;
      if (evt->subevent == EVT_LE_CONN_COMPLETE) {
        evt_le_connection_complete* cc = (evt_le_connection_complete*)evt->data;
        connected = true;
        notifyOn = false;                 // wait for the Pi to switch notifications on
        connHandle = cc->handle;
      }
      break;
    }

    case EVT_VENDOR: {
      evt_blue_aci* blueEvt = (evt_blue_aci*)eventPckt->data;
      if (blueEvt->ecode == EVT_BLUE_GATT_READ_PERMIT_REQ) {
        if (connHandle != 0) aci_gatt_allow_read(connHandle);
      } else if (blueEvt->ecode == EVT_BLUE_GATT_ATTRIBUTE_MODIFIED) {
        evt_gatt_attr_modified_IDB05A1* evt = (evt_gatt_attr_modified_IDB05A1*)blueEvt->data;
        if (evt->attr_handle == uartWriteHandle + 1) {
          onWrite(evt->data_length, evt->att_data);
        } else if (evt->attr_handle == uartNotifyHandle + 2 && evt->data_length >= 1) {
          notifyOn = (evt->att_data[0] & 0x01) != 0;   // CCCD: bit 0 = notifications on
        }
      }
      break;
    }
  }
}
