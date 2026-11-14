"""SouthpawOS hub command line.

    python -m southpaw play boards.toml    # THE GAME: card tap + 5 bag hits to start, rounds, upload
    python -m southpaw play --sim          # the game with fake boards and a fake player
    python -m southpaw play --sim --card 04A2B91C   # ...who taps this NFC card (rounds go to the Telegram bot)
    python -m southpaw sim                 # quick demo loop with fake boards
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
import os
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


def _make_uploader(args: argparse.Namespace):
    """Jovis's dashboard/backend/pi_uploader.py, if a website server was given."""
    if not args.server:
        return None
    if not args.token:
        sys.exit("--server needs the website's INGEST_TOKEN: pass --token or set INGEST_TOKEN.")
    backend = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
    sys.path.insert(0, str(backend))
    from pi_uploader import Uploader  # noqa: E402

    return Uploader(args.server, args.token, queue_file=str(Path(args.log_dir) / "unsent_rounds.jsonl"))


async def _play(args: argparse.Namespace) -> None:
    from .game import Game, GameConfig

    if args.sim:
        from .sim import SimConfig, Simulator

        transport = Simulator(SimConfig(stance=args.stance, vision=not args.no_vision, guard=not args.no_vision,
                                        seed=args.seed, card=args.card))
    else:
        from .ble import BleTransport

        if not args.config:
            sys.exit("Give boards.toml, or use --sim.")
        transport = BleTransport(_load_addresses(Path(args.config)))

    hub = Hub(transport, Fuser(use_vision=not args.no_vision), log_dir=Path(args.log_dir))
    store = None
    if not args.no_db:
        from .db import Store

        store = Store(args.db)
    uploader = _make_uploader(args)
    notifier = None
    if not args.no_telegram:  # Johanan's Telegram bot: harmless if it isn't running (UDP)
        from .notify import BotNotifier

        notifier = BotNotifier(port=args.telegram_port)
    cfg = GameConfig(
        rounds=args.rounds,
        round_secs=args.round_secs,
        rest_secs=args.rest_secs,
        summary_secs=args.summary_secs,
        stance=args.stance,
        seed=args.seed,
    )
    game = Game(hub, cfg, store=store, uploader=uploader, notifier=notifier, source="sim" if args.sim else "ble")

    synced = asyncio.Event()
    boards = ("wristL", "wristR", "bag") if args.sim else tuple(transport.addresses)

    async def on_connect(board: str) -> None:
        await hub.sync_clock(board)
        if all(hub.clocks.get(b) and hub.clocks[b].synced for b in boards):
            synced.set()
        if game.state == "wait" and board == "bag":
            await hub.send("bag", "M,WAIT\n")  # bag reconnected: show the start screen again

    udp = [] if args.sim else await listen_local(hub, host=args.listen_host)
    runner = asyncio.create_task(transport.start(hub.on_line, on_connect))
    try:
        print(f"waiting for {', '.join(boards)} to connect...")
        await synced.wait()
        print("all boards connected" + (f", uploading to {args.server}" if uploader else ", no website upload"))
        await game.run_forever(args.games)
    finally:
        runner.cancel()
        for t in udp:
            t.close()
        if store:
            store.close()
        if notifier:
            notifier.close()
        hub.close()


async def _main(args: argparse.Namespace) -> None:
    if args.cmd == "play":
        await _play(args)
        return
    if args.cmd == "scan":
        from .ble import scan

        await scan(args.timeout)
        return

    if args.cmd == "sim":
        from .sim import SimConfig, Simulator

        transport = Simulator(SimConfig(stance=args.stance, vision=not args.no_vision, guard=not args.no_vision,
                                        seed=args.seed))
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

    g = sub.add_parser("play", help="run the real game (card tap + 5 bag hits to start)")
    g.add_argument("config", nargs="?", help="boards.toml with the board addresses")
    g.add_argument("--sim", action="store_true", help="fake boards and a fake player instead of BLE")
    g.add_argument("--rounds", type=int, default=3)
    g.add_argument("--round-secs", type=float, default=30.0)
    g.add_argument("--rest-secs", type=float, default=20.0)
    g.add_argument("--summary-secs", type=float, default=10.0)
    g.add_argument("--games", type=int, default=None, help="stop after this many games (default: keep going)")
    g.add_argument("--stance", choices=("orthodox", "southpaw"), default="orthodox")
    g.add_argument("--no-vision", action="store_true")
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--log-dir", default="logs")
    g.add_argument("--db", default="southpaw.db")
    g.add_argument("--no-db", action="store_true")
    g.add_argument("--server", default=os.environ.get("SOUTHPAW_SERVER"),
                   help="website address, e.g. http://192.168.137.1:5000 (or set SOUTHPAW_SERVER)")
    g.add_argument("--token", default=os.environ.get("INGEST_TOKEN"), help="the website's INGEST_TOKEN")
    g.add_argument("--listen-host", default="127.0.0.1",
                   help="0.0.0.0 to accept vision/NFC messages from other machines")
    g.add_argument("--card", help="with --sim: the simulated player taps this NFC card UID (default: guest)")
    g.add_argument("--telegram-port", type=int, default=5007, help="UDP port of the Telegram bot on this Pi")
    g.add_argument("--no-telegram", action="store_true", help="don't send rounds to the Telegram bot")

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
