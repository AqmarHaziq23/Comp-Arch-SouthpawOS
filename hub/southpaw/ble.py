"""Real hardware: connect to the TinyCircuits boards over BLE (Nordic UART).

Based on bleak's examples `uart_service.py`, `two_devices.py` and
`disconnect_callback.py` (https://github.com/hbldh/bleak, MIT licence).

Every TinyCircuits BLE shield advertises as "BlueNRG", so boards are told
apart by address. Run `python -m southpaw scan` to find them, then put the
addresses in boards.toml.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from . import protocol as proto

log = logging.getLogger(__name__)

OnLine = Callable[[str, str, float], Awaitable[None]]
OnConnect = Callable[[str], Awaitable[None]]


class BleTransport:
    def __init__(self, addresses: dict[str, str], reconnect_delay: float = 2.0) -> None:
        self.addresses = addresses  # board name -> BLE address
        self.reconnect_delay = reconnect_delay
        self._clients: dict[str, object] = {}
        # BlueZ copes badly with several connects at once, so connect one at a time.
        self._connect_lock = asyncio.Lock()

    async def start(self, on_line: OnLine, on_connect: OnConnect | None = None) -> None:
        await asyncio.gather(*(self._keep_connected(b, a, on_line, on_connect) for b, a in self.addresses.items()))

    async def send(self, board: str, line: str) -> None:
        client = self._clients.get(board)
        if client is None or not client.is_connected:
            log.warning("%s not connected, dropped %r", board, line.strip())
            return
        data = line.encode("ascii")
        if len(data) > proto.MAX_BLE_PAYLOAD:
            raise proto.ProtocolError(f"{line!r} is longer than one BLE packet")
        await client.write_gatt_char(proto.NUS_RX_UUID, data)

    def is_connected(self, board: str) -> bool:
        c = self._clients.get(board)
        return bool(c and c.is_connected)

    async def _keep_connected(self, board: str, address: str, on_line: OnLine, on_connect: OnConnect | None) -> None:
        from bleak import BleakClient  # imported here so the simulator works without bleak

        delay = self.reconnect_delay
        while True:
            disconnected = asyncio.Event()
            assembler = proto.LineAssembler()

            def handle_disconnect(_client, board=board):
                log.warning("%s disconnected", board)
                disconnected.set()

            async def handle_rx(_char, data: bytearray, board=board, assembler=assembler):
                t = time.monotonic()
                for line in assembler.feed(bytes(data)):
                    await on_line(board, line, t)

            client = BleakClient(address, disconnected_callback=handle_disconnect)
            try:
                async with self._connect_lock:
                    log.info("connecting to %s (%s)", board, address)
                    await client.connect(timeout=15.0)
                    await client.start_notify(proto.NUS_TX_UUID, handle_rx)
                self._clients[board] = client
                log.info("%s connected", board)
                delay = self.reconnect_delay
                if on_connect:
                    await on_connect(board)
                await disconnected.wait()
            except asyncio.CancelledError:
                if client.is_connected:
                    await client.disconnect()
                raise
            except Exception as e:  # BLE errors vary a lot by platform
                log.warning("%s: %s; retrying in %.0fs", board, e, delay)
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            finally:
                self._clients.pop(board, None)
            await asyncio.sleep(0.5)  # brief pause before reconnecting


async def scan(timeout: float = 6.0) -> None:
    """Print nearby BLE devices. TinyCircuits boards show up as 'BlueNRG'."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -da[1].rssi)
    print(f"{'address':<20} {'rssi':>5}  {'nus':<4} name")
    for device, adv in rows:
        nus = "yes" if proto.NUS_SERVICE_UUID in [u.lower() for u in adv.service_uuids] else ""
        print(f"{device.address:<20} {adv.rssi:>5}  {nus:<4} {device.name or adv.local_name or ''}")
    print("\nPut the TinyCircuits boards (usually 'BlueNRG') into boards.toml.")
