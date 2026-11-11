"""SouthpawOS hub command line.

    python -m southpaw sim                 # full demo with fake boards
    python -m southpaw scan                # find the TinyCircuits boards
    python -m southpaw ble boards.toml     # demo with the real boards
    python -m southpaw ble boards.toml --listen   # just print what boards send
    python -m southpaw leaderboard         # best score per player
    python -m southpaw player 04A2B91C "Yue Ming"  # name an NFC card
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


def _db_commands(args: argparse.Namespace) -> None:
    from .db import Store

    store = Store(args.db)
    try:
        if args.cmd == "player":
            store.add_player(args.uid, args.name, args.stance)
            print(f"Saved {args.uid} as {args.name} ({args.stance}).")
            return
        rows = store.leaderboard(args.limit, include_sim=args.include_sim)
        if not rows:
            print("No finished sessions yet." + ("" if args.include_sim else " (add --include-sim to show simulator runs)"))
        for i, r in enumerate(rows, 1):
            n = r["sessions"]
            print(f"{i:>2}. {r['name']:<20} best {r['best']:>4}   ({n} session{'s' if n != 1 else ''})")
    finally:
        store.close()


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
    store = None
    if not args.no_db:
        from .db import Store

        store = Store(args.db)
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
        await run_demo(
            hub,
            rounds=args.rounds,
            combos_per_round=args.combos,
            stance=args.stance,
            seed=args.seed,
            store=store,
            source=args.cmd,
        )
    finally:
        runner.cancel()
        if store:
            store.close()
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
        c.add_argument("--db", default="southpaw.db", help="SQLite file for players and scores")
        c.add_argument("--no-db", action="store_true", help="don't save to the database")

    lb = sub.add_parser("leaderboard", help="best score per player")
    lb.add_argument("--limit", type=int, default=10)
    lb.add_argument("--include-sim", action="store_true", help="include simulator sessions")
    lb.add_argument("--db", default="southpaw.db")

    pl = sub.add_parser("player", help="name an NFC card")
    pl.add_argument("uid", help="NFC card UID")
    pl.add_argument("name")
    pl.add_argument("--stance", choices=("orthodox", "southpaw"), default="orthodox")
    pl.add_argument("--db", default="southpaw.db")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd in ("leaderboard", "player"):
        _db_commands(args)
        return
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
