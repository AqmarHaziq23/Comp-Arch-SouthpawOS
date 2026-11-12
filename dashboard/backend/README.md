# SouthpawOS backend - Pi side

Runs on the Raspberry Pi next to the hub. After every round it sends the round's data and the player's NFC card ID
to the website (`../frontend`), which saves it in its database.

One file, [pi_uploader.py](pi_uploader.py). It uses only the Python standard library, so there is nothing to install.

## Use it from the hub

    from pi_uploader import Uploader

    up = Uploader("http://<server>:5000", token="<INGEST_TOKEN>")

    up.start_session(card_uid="04A2B91C")     # the card that was tapped; None = guest
    up.send_round(1, score=120, thrown=18, landed=14,
                  combos_called=6, combos_hit=2, best_streak=3,    # optional
                  hr_bpm=132, hr_good=True)                        # optional: rest reading after the round
    up.send_round(2, score=90, thrown=15, landed=10)
    up.end_session()

- `<server>` is the address of the machine running `docker compose up` in `../frontend`.
- `<INGEST_TOKEN>` is the value in that machine's `.env`. Keep it out of git.
- The card ID is what links a round to a player: the website matches it to the athlete account with that card.
- The calls wait for the network (up to 5 s). In the hub's asyncio loop use
  `await asyncio.to_thread(up.send_round, ...)` so the game does not pause.

## If the network drops

Every round is first written to `unsent_rounds.jsonl` on the Pi, then uploaded. If the upload fails, the round stays in
that file and is sent again on the next upload, at `end_session()`, or with:

    python pi_uploader.py --server http://<server>:5000 --token <INGEST_TOKEN> --flush

Sending a round twice is safe: the website replaces the earlier copy. A round the website refuses as invalid
(HTTP 400) is dropped and logged as an error, since retrying it would never work.

The website's "Latest round" page shows each round a few seconds after it is uploaded. There is no separate live
feed to send.

## Test the link without the hub

    python pi_uploader.py --server http://localhost:5000 --token <INGEST_TOKEN> --card 04A2B91C

This sends a made-up 3-round session for that card (leave out `--card` for a guest) and says whether each round
arrived. With the website's `--sim` mode, `04A2B91C` is the demo athlete Kai.

## Still to do (Member C)

Call `start_session`, `send_round` and `end_session` from the hub's game loop. The hub already knows the tapped
card (`Hub.player_uid`) and the per-round punches; it needs to count thrown and landed and add up the round's score.
