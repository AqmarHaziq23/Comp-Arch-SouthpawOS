/*
 * Based on: TinyCircuits ST BLE TinyShield UARTPassThrough example (UART.ino) by Ben Rose,
 *   TinyCircuits, STBLE library, https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) – advertises as BLE_NAME with a per-hand address, answers the
 *   protocol's T clock ping, queues outgoing protocol lines, no blocking waits or debug sprintf.
 *
 * Nordic UART Service as in protocol/PROTOCOL.md: the board notifies on 6E400003,
 * the Pi writes to 6E400002.
 */
#ifndef SOUTHPAW_BLE_H
#define SOUTHPAW_BLE_H

#include <stdint.h>

constexpr uint8_t BLE_MAX_MSG = 20;        // one BLE packet, including the trailing '\n'

struct BleStats {
  uint32_t maxPollUs;    // longest ble_poll() call
  uint32_t maxSendUs;    // longest single send
  uint32_t sent;         // protocol lines sent (not counting T replies)
  uint32_t pings;        // T pings answered
  uint32_t failed;       // sends the BlueNRG refused (retried, then dropped)
  uint32_t dropped;      // lines dropped: not connected, queue full or too many failures
  uint32_t ignoredRx;    // lines from the Pi that weren't T
};

bool ble_begin();                          // setup() only. False if the BlueNRG didn't answer.
void ble_poll();                           // every loop pass: events, T replies, queued sends
bool ble_connected();                      // link up AND notifications on: sends will arrive
bool ble_link_up();                        // link up (notifications may not be on yet)
// Queue one protocol line (must end with '\n', max BLE_MAX_MSG bytes). False = dropped.
bool ble_queue_line(const char* line, uint8_t len);
void ble_address(char out[18]);            // advertised address as "AA:BB:CC:DD:EE:FF"
void ble_take_stats(BleStats& out);        // read and reset

#endif
