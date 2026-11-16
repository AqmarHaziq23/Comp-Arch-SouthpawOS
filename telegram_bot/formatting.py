"""
formatting.py - Southpaw OS Telegram bot

Turns a round's stats (a dict from the hub) into a nicely laid-out Telegram message.

The hub builds the stats in hub/southpaw/notify.py (round_stats). The names match what
the website gets, so the phone and the website always show the same numbers:

{
  "round": 1, "rounds": 3,             this is round 1 of 3
  "duration_s": 30,                    round length
  "score": 120,
  "thrown": 18, "landed": 14,          punches thrown / punches that hit the bag
  "combos_called": 6, "combos_hit": 4, combos the bag called / combos thrown correctly
  "best_streak": 3,                    most correct combos in a row
  "by_type": {"jab": {"thrown": 5, "landed": 4}, "cross": {...}, ...},
  "hr_bpm": 142, "hr_good": true,      rest heart rate after the round (only if measured)
  "guard_pct": 84,                     % of the round the hands were up in guard (both hands together; punches left out)
  "guard_pct_lead": 90, "guard_pct_rear": 86, "guard_drops": 2      (only if vision was running)
}

It is deliberately forgiving:
  * every field is optional - lines are only shown for the stats that are present
  * any EXTRA fields the hub sends later (e.g. guard stats from vision) still appear
    under "Other stats", so new data shows up without changing this code first

Messages use Telegram's HTML formatting (<b>bold</b>, <pre>fixed-width</pre>), so any
text that comes from outside (names, keys) is escaped first.
"""

import html

# Fields this file lays out itself; everything else goes under "Other stats"
KNOWN_FIELDS = {"round", "rounds", "duration_s", "score", "thrown", "landed", "combos_called",
                "combos_hit", "best_streak", "by_type", "hr_bpm", "hr_good",
                "guard_pct", "guard_pct_lead", "guard_pct_rear", "guard_drops"}

# Order of the rows in the per-punch table ("unknown" = the wrist board couldn't tell)
PUNCH_ORDER = ["jab", "cross", "hook", "uppercut", "unknown"]


def _pct(part, whole):
    """'83%' or '' if it can't be worked out."""
    try:
        return f"{100 * float(part) / float(whole):.0f}%" if float(whole) > 0 else ""
    except (TypeError, ValueError):
        return ""


def _num(value):
    """Show numbers nicely: 1240 -> '1,240', 12.345 -> '12.3'. Anything else as text."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() else f"{value:,.1f}"
    return str(value)


def _duration(seconds):
    """125 -> '2:05'."""
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return str(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _pretty_key(key):
    """'best_combo_streak' -> 'Best combo streak'."""
    return str(key).replace("_", " ").strip().capitalize()


def format_round(data, first_name=None):
    """Build the round-summary message (Telegram HTML) from the hub's stats."""
    esc = html.escape

    # --- Title: "Round 1 of 3 complete!" (or just "Round complete!" if the number is missing)
    title = "Round complete!"
    if "round" in data:
        title = f"Round {_num(data['round'])}" + (f" of {_num(data['rounds'])}" if "rounds" in data else "") \
                + " complete!"
    lines = [f"🥊 <b>{esc(title)}</b>"]
    if first_name:
        lines.append(f"Nice work, {esc(first_name)}.")
    lines.append("")
    header_lines = len(lines)

    # --- Headline numbers (each line only if the data has it) ---------------------------
    if "score" in data:
        lines.append(f"🏆 Score: <b>{_num(data['score'])}</b>")
    if "thrown" in data or "landed" in data:
        landed, thrown = data.get("landed"), data.get("thrown")
        if landed is not None and thrown is not None:
            accuracy = _pct(landed, thrown)
            lines.append(f"👊 Punches: {_num(landed)} landed / {_num(thrown)} thrown"
                         + (f" ({accuracy})" if accuracy else ""))
        else:
            lines.append(f"👊 Punches thrown: {_num(thrown if thrown is not None else landed)}")
    if "combos_hit" in data:
        called = data.get("combos_called")
        lines.append(f"🎯 Combos: {_num(data['combos_hit'])}"
                     + (f"/{_num(called)} correct" if called is not None else " correct"))
    if "best_streak" in data:
        lines.append(f"🔥 Best streak: {_num(data['best_streak'])} in a row")
    if "hr_bpm" in data:
        unreliable = data.get("hr_good") is False     # the sensor said the reading was shaky
        lines.append(f"❤️ Rest heart rate: {_num(data['hr_bpm'])} bpm" + (" (rough reading)" if unreliable else ""))
    if "guard_pct" in data or "guard_drops" in data:
        # e.g. "🛡️ Guard up: 84% (lead 90% · rear 86%) · 2 drops"
        parts = []
        if "guard_pct" in data:
            hands = [f"{name} {_num(data[key])}%" for name, key in (("lead", "guard_pct_lead"),
                                                                    ("rear", "guard_pct_rear")) if key in data]
            parts.append(f"{_num(data['guard_pct'])}%" + (f" ({' · '.join(hands)})" if hands else ""))
        if "guard_drops" in data:
            drops = data["guard_drops"]
            parts.append(f"{_num(drops)} drop{'' if drops == 1 else 's'}")
        lines.append(f"🛡️ Guard up: {' · '.join(parts)}")
    if "duration_s" in data:
        lines.append(f"⏱ Round time: {_duration(data['duration_s'])}")

    # --- Per-punch table (fixed-width so the columns line up) ----------------------------
    by_type = data.get("by_type")
    if isinstance(by_type, dict) and by_type:
        names = [p for p in PUNCH_ORDER if p in by_type] + [p for p in by_type if p not in PUNCH_ORDER]
        table = [f"{'Punch':<10}{'Landed':>7}{'Thrown':>7}"]
        for name in names:
            stats = by_type[name] if isinstance(by_type[name], dict) else {"thrown": by_type[name]}
            table.append(f"{_pretty_key(name)[:10]:<10}{_num(stats.get('landed', '-')):>7}"
                         f"{_num(stats.get('thrown', '-')):>7}")
        lines += ["", "<b>By punch</b>", "<pre>" + esc("\n".join(table)) + "</pre>"]

    # --- Anything else the hub sent (new stats appear here automatically) -----------------
    extras = [(k, v) for k, v in data.items() if k not in KNOWN_FIELDS and not isinstance(v, (dict, list))]
    if extras:
        lines += ["", "<b>Other stats</b>"]
        lines += [f"• {esc(_pretty_key(k))}: {esc(_num(v))}" for k, v in extras]

    if len(lines) == header_lines:     # nothing but the title: the hub sent no stats we could show
        lines.append("(No stats were recorded for this round.)")
    return "\n".join(lines)
