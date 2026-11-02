"""SouthpawOS hub command line.

    python -m southpaw sim                 # full demo with fake boards
    python -m southpaw scan                # find the TinyCircuits boards
    python -m southpaw ble boards.toml     # demo with the real boards
    python -m southpaw ble boards.toml --listen   # just print what boards send
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .fusion import Fuser
from .hub import Hub, listen_local, run_demo


def _load_addresses(path: Path) -> dict[str, str]:
    import tomllib  # Python 3.11+

    with path.open("rb") as f:
        boards = tomllib.load(f).get("boards", {})
    boards = {k: v for k, v in boards.items() if v and "XX" not in v}
    if not boards:
        sys.exit(f"No board addresses in {path}. Run `python -m southpaw scan` and fill them in.")
    return boards


async def _main(args: argparse.Namespace) -> None:
    if args.cmd == "scan":
        from .ble import scan

        await scan(args.timeout)
        return

    if args.cmd == "sim":
        from .sim import SimConfig, Simulator

        transport = Simulator(SimConfig(stance=args.stance, vision=not args.no_vision, seed=args.seed))
    else:
        from .ble import BleTransport

        transport = BleTransport(_load_addresses(Path(args.config)))

    hub = Hub(transport, Fuser(use_vision=not args.no_vision), log_dir=Path(args.log_dir))
    if args.cmd == "ble" and args.listen:
        async def show(board, line, t):
            print(f"{t:10.3f}  {board:<7} {line}")
            await hub.on_line(board, line, t)
        on_line = show
    else:
        on_line = hub.on_line

    synced = asyncio.Event()

    async def on_connect(board: str) -> None:
        await hub.sync_clock(board)
        if args.cmd == "sim" or all(transport.is_connected(b) for b in transport.addresses):
            synced.set()

    udp = await listen_local(hub) if args.cmd == "ble" else []
    runner = asyncio.create_task(transport.start(on_line, on_connect))
    try:
        if args.cmd == "ble" and args.listen:
            await runner
            return
        print("waiting for boards to connect and sync clocks...")
        await synced.wait()
        await run_demo(hub, rounds=args.rounds, combos_per_round=args.combos, stance=args.stance, seed=args.seed)
    finally:
        runner.cancel()
        for t in udp:
            t.close()
        hub.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="southpaw", description="SouthpawOS hub")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list nearby BLE devices")
    s.add_argument("--timeout", type=float, default=6.0)

    for name, help_ in (("sim", "run the demo with simulated boards"), ("ble", "run with the real boards")):
        c = sub.add_parser(name, help=help_)
        if name == "ble":
            c.add_argument("config", help="boards.toml with the board addresses")
            c.add_argument("--listen", action="store_true", help="only print incoming messages")
        c.add_argument("--rounds", type=int, default=2)
        c.add_argument("--combos", type=int, default=4, help="combos per round")
        c.add_argument("--stance", choices=("orthodox", "southpaw"), default="orthodox")
        c.add_argument("--no-vision", action="store_true", help="run without camera events")
        c.add_argument("--seed", type=int, default=None)
        c.add_argument("--log-dir", default="logs")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
