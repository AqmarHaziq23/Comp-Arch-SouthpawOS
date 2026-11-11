#!/usr/bin/env python3
# Based on: bleak example uart_service.py, Henrik Blidh and contributors, https://github.com/hbldh/bleak (MIT)
# Modified by: Yue Ming (Team P1D) – finds a SouthpawOS wrist board by name, prints every message with a
#   timestamp, checks each one with the hub's own protocol parser, and can send T clock pings like the hub.
"""Listen to a SouthpawOS wrist board over Bluetooth from a laptop, without the Pi.

Usage examples (from the repo root):
    python training/tools/ble_listen.py --scan                 # list nearby boards
    python training/tools/ble_listen.py --hand L               # connect to P1D-WRIST-L and print messages
    python training/tools/ble_listen.py --hand L --ping 5      # also test the T clock reply, like the hub
    python training/tools/ble_listen.py --hand L --log ble.jsonl --seconds 60

Each received line is printed as
    14:03:12.481  +12.345s  P,L,X,48213,11    REJECTED by hub: unknown punch type 'X'
and checked with hub/southpaw/protocol.py, so "ok" means the Pi would accept it.
Ctrl+C to stop. Needs:  pip install bleak
"""
import argparse
import asyncio
import datetime
import json
import pathlib
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak is missing. Install it with:  pip install bleak")

# Use the hub's parser and UUIDs (protocol/PROTOCOL.md in code) instead of copying them here.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "hub"))
from southpaw import protocol as proto  # noqa: E402


def stamp(t0):
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    return f"{now}  +{time.monotonic() - t0:7.3f}s"


def check(line):
    if len(line.encode("ascii", errors="replace")) + 1 > proto.MAX_BLE_PAYLOAD:
        return f"TOO LONG ({len(line) + 1} bytes with \\n, max {proto.MAX_BLE_PAYLOAD})"
    try:
        return f"ok {proto.parse_board(line)}"
    except proto.ProtocolError as e:
        return f"REJECTED by hub: {e}"


async def scan(seconds, show_all):
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -da[1].rssi)
    hidden = 0
    print(f"{'address':<20} {'rssi':>5}  {'nus':<4} name")
    for device, adv in rows:
        name = adv.local_name or device.name or ""
        if not name and not show_all:
            hidden += 1
            continue
        nus = "yes" if proto.NUS_SERVICE_UUID in [u.lower() for u in adv.service_uuids] else ""
        mark = "   <- SouthpawOS" if name.startswith("P1D-") else ""
        print(f"{device.address:<20} {adv.rssi:>5}  {nus:<4} {name}{mark}")
    if hidden:
        print(f"({hidden} devices without a name hidden; --all shows them)")


async def find(args):
    if args.address:
        return args.address
    name = args.name or f"P1D-WRIST-{args.hand}"
    print(f"Looking for {name} ...")
    dev = await BleakScanner.find_device_by_name(name, timeout=args.timeout)
    if dev is None:
        sys.exit(f"{name} not found. Is the board on and not connected to something else? Try --scan.")
    print(f"Found {name} at {dev.address}")
    return dev


async def listen(args):
    target = await find(args)
    t0 = time.monotonic()
    log = open(args.log, "a") if args.log else None
    assembler = proto.LineAssembler()
    replies = asyncio.Queue()
    counts = {"lines": 0, "ok": 0, "rejected": 0}

    def on_rx(_char, data: bytearray):
        t_recv = time.monotonic()
        for line in assembler.feed(bytes(data)):
            verdict = check(line)
            counts["lines"] += 1
            counts["ok" if verdict.startswith("ok") else "rejected"] += 1
            print(f"{stamp(t0)}  {line:<20}  {verdict}")
            if log:
                log.write(json.dumps({"t": round(t_recv, 4), "wall": datetime.datetime.now().isoformat(),
                                      "line": line, "check": verdict}) + "\n")
                log.flush()
            if line.startswith("T,"):
                replies.put_nowait((t_recv, line))

    disconnected = asyncio.Event()
    client = BleakClient(target, disconnected_callback=lambda _c: disconnected.set())
    await client.connect()
    print(f"{stamp(t0)}  connected. Waiting for messages (Ctrl+C to stop)")
    try:
        await client.start_notify(proto.NUS_TX_UUID, on_rx)
        if args.ping:
            await ping(client, replies, args.ping)
        try:
            await asyncio.wait_for(disconnected.wait(), args.seconds)
            print(f"{stamp(t0)}  board disconnected")
        except asyncio.TimeoutError:
            print(f"{stamp(t0)}  {args.seconds:g} s up")
    finally:
        if client.is_connected:
            await client.disconnect()
        if log:
            log.close()
        print(f"{counts['lines']} lines: {counts['ok']} ok, {counts['rejected']} rejected/too long")


async def ping(client, replies, n):
    """Same steps as the hub's sync_clock(): send T, time the T,<ms> reply, keep the shortest round trip."""
    best = None
    for i in range(n):
        while not replies.empty():
            replies.get_nowait()
        t_send = time.monotonic()
        await client.write_gatt_char(proto.NUS_RX_UUID, proto.clock_ping_msg().encode("ascii"), response=False)
        try:
            t_recv, line = await asyncio.wait_for(replies.get(), 1.0)
        except asyncio.TimeoutError:
            print(f"  ping {i + 1}: no T reply within 1 s")
            continue
        board_ms = int(line.split(",")[1])
        rtt = t_recv - t_send
        offset = (t_send + t_recv) / 2 - board_ms / 1000
        print(f"  ping {i + 1}: round trip {rtt * 1000:.0f} ms, board_ms {board_ms}")
        if best is None or rtt < best[0]:
            best = (rtt, offset)
        await asyncio.sleep(0.05)
    if best:
        print(f"  best round trip {best[0] * 1000:.0f} ms -> offset {best[1]:.3f} s (what the hub would use)")


def main():
    ap = argparse.ArgumentParser(description="Print a SouthpawOS board's Bluetooth messages with timestamps.")
    ap.add_argument("--scan", action="store_true", help="list nearby named Bluetooth devices and exit")
    ap.add_argument("--all", action="store_true", help="with --scan, also list devices without a name")
    ap.add_argument("--hand", default="L", choices=["L", "R"], help="connect to P1D-WRIST-<hand> (default L)")
    ap.add_argument("--name", help="connect to this advertised name instead")
    ap.add_argument("--address", help="connect to this address instead of searching by name")
    ap.add_argument("--ping", type=int, default=0, help="send this many T clock pings after connecting")
    ap.add_argument("--log", help="also append every line to this JSON-lines file")
    ap.add_argument("--seconds", type=float, default=None, help="stop after this long (default: run until Ctrl+C)")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to search / scan (default 10)")
    args = ap.parse_args()
    try:
        asyncio.run(scan(args.timeout, args.all) if args.scan else listen(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
