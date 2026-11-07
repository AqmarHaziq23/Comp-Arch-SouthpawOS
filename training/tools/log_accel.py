#!/usr/bin/env python3
# Modified by: Yue Ming (Team P1D) – records the wrist board's accelerometer stream to labelled CSV files.
"""Record the wrist board's accelerometer stream to a labelled CSV file.

Usage examples:
    python log_accel.py --label jab --hand L --person ym
    python log_accel.py --label hook --hand L --person ym --seconds 30
    python log_accel.py --list            # show serial ports

Each run saves one file, e.g. training/data/raw/ym_L_jab_20261108-143012.csv, with columns
    board_ms, x_raw, y_raw, z_raw, x_g, y_g, z_g
Close the Arduino IDE Serial Monitor first: only one program can use the port.

Team P1D - written for the ICT1011 TinyCircuit project.
"""
import argparse
import csv
import datetime
import pathlib
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is missing. Install it with:  pip install pyserial")

COUNTS_PER_G = 32.0  # BMA250 at +/-16 g
BAUD = 115200


def list_serial_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found. Is the board plugged in and switched ON?")
    for p in ports:
        print(f"  {p.device:<22} {p.description}")
    return ports


def find_port():
    ports = list(list_ports.comports())
    for p in ports:
        text = f"{p.description} {p.manufacturer or ''} {p.product or ''}".lower()
        if "tinyscreen" in text or "tinycircuits" in text:
            return p.device
    if len(ports) == 1:
        return ports[0].device
    print("Couldn't pick the board automatically. Ports found:")
    list_serial_ports()
    sys.exit("Run again with --port <port>, e.g. --port COM5 or --port /dev/cu.usbmodem1101")


def main():
    ap = argparse.ArgumentParser(description="Log TinyScreen+ accelerometer data to CSV.")
    ap.add_argument("--label", help="what you're recording: jab, cross, hook, uppercut, idle, ...")
    ap.add_argument("--hand", default="L", choices=["L", "R"], help="which wrist the board is on")
    ap.add_argument("--person", default="ym", help="short name of the person punching")
    ap.add_argument("--seconds", type=float, default=20.0, help="recording length")
    ap.add_argument("--countdown", type=int, default=3, help="seconds to get into stance")
    ap.add_argument("--port", help="serial port (auto-detected if left out)")
    ap.add_argument("--outdir", default=str(pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"),
                    help="folder for CSV files (default: training/data/raw)")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args()

    if args.list:
        list_serial_ports()
        return
    if not args.label:
        ap.error("--label is required (e.g. --label jab)")

    port = args.port or find_port()
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outdir / f"{args.person}_{args.hand}_{args.label}_{stamp}.csv"

    print(f"Opening {port} ...")
    with serial.Serial(port, BAUD, timeout=1) as ser:
        time.sleep(0.5)
        ser.reset_input_buffer()
        for i in range(args.countdown, 0, -1):
            print(f"  starting in {i} ...")
            time.sleep(1)
        print(f"GO - recording '{args.label}' ({args.hand} hand) for {args.seconds:g} s")

        n, bad, first_ms, last_ms = 0, 0, None, None
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["board_ms", "x_raw", "y_raw", "z_raw", "x_g", "y_g", "z_g"])
            start = time.time()
            while time.time() - start < args.seconds:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("ERROR"):
                    sys.exit(f"Board says: {line}")
                parts = line.split(",")
                if len(parts) != 4:
                    bad += 1
                    continue
                try:
                    ms, x, y, z = (int(v) for v in parts)
                except ValueError:
                    bad += 1
                    continue
                w.writerow([ms, x, y, z,
                            round(x / COUNTS_PER_G, 3), round(y / COUNTS_PER_G, 3), round(z / COUNTS_PER_G, 3)])
                n += 1
                if first_ms is None:
                    first_ms = ms
                last_ms = ms

    if n < 2 or last_ms == first_ms:
        print(f"Only {n} samples received. Check the sketch is uploaded and the Serial Monitor is closed.")
        return
    rate = (n - 1) / ((last_ms - first_ms) / 1000.0)
    print(f"Saved {n} samples to {path}  (about {rate:.0f} per second, {bad} bad lines)")


if __name__ == "__main__":
    main()
