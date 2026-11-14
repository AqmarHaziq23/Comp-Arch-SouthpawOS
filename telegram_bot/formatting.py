"""
formatting.py - Southpaw OS Telegram bot

Turns a round's stats (a dict from the hub) into a nicely laid-out Telegram message.

The exact stats aren't final yet, so this is deliberately forgiving:
  * every field is optional - lines are only shown for the stats that are present
  * any EXTRA fields the hub sends (that this file doesn't know about yet) are still
    shown under "Other stats", so new data appears without changing this code

Messages use Telegram's HTML formatting (<b>bold</b>, <pre>fixed-width</pre>), so any
text that comes from outside (names, keys) is escaped first.

Expected shape (see sample_round.json; all optional):
{
  "duration_s": 120,
  "punches_thrown": 86, "punches_landed": 71,
  "by_type": {"jab": {"thrown": 30, "landed": 27}, "cross": {...}, "hook": {...}, "uppercut": {...}},
  "combos_called": 15, "combos_completed": 12,
  "score": 1240,
  "heart_rate_avg": 152, "heart_rate_max": 178,
  ...anything else...
}
"""

import html

# Fields this file lays out itself; everything else goes under "Other stats"
KNOWN_FIELDS = {"duration_s", "punches_thrown", "punches_landed", "by_type", "combos_called",
                "combos_completed", "score", "heart_rate_avg", "heart_rate_max", "round_id",
                "started_at", "ended_at"}

PUNCH_ORDER = ["jab", "cross", "hook", "uppercut"]


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
        return f"{value:,.1f}"
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
    lines = ["🥊 <b>Round complete!</b>"]
    if first_name:
        lines.append(f"Nice work, {esc(first_name)}.")
    lines.append("")

    # --- Headline numbers (each line only if the data has it) ---------------------------
    if "duration_s" in data:
        lines.append(f"⏱ Time: {_duration(data['duration_s'])}")
    if "punches_landed" in data or "punches_thrown" in data:
        landed, thrown = data.get("punches_landed"), data.get("punches_thrown")
        if landed is not None and thrown is not None:
            accuracy = _pct(landed, thrown)
            lines.append(f"👊 Punches: {_num(landed)} landed / {_num(thrown)} thrown"
                         + (f" ({accuracy})" if accuracy else ""))
        else:
            lines.append(f"👊 Punches thrown: {_num(thrown if thrown is not None else landed)}")
    if "combos_completed" in data:
        called = data.get("combos_called")
        lines.append(f"🎯 Combos: {_num(data['combos_completed'])}"
                     + (f"/{_num(called)} completed" if called is not None else " completed"))
    if "score" in data:
        lines.append(f"🏆 Score: {_num(data['score'])}")
    if "heart_rate_avg" in data or "heart_rate_max" in data:
        parts = []
        if "heart_rate_avg" in data:
            parts.append(f"avg {_num(data['heart_rate_avg'])}")
        if "heart_rate_max" in data:
            parts.append(f"max {_num(data['heart_rate_max'])}")
        lines.append(f"❤️ Heart rate: {' · '.join(parts)} bpm")

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

    # --- Anything else the hub sent ------------------------------------------------------
    extras = [(k, v) for k, v in data.items() if k not in KNOWN_FIELDS and not isinstance(v, (dict, list))]
    if extras:
        lines += ["", "<b>Other stats</b>"]
        lines += [f"• {esc(_pretty_key(k))}: {esc(_num(v))}" for k, v in extras]

    if len(lines) <= 3:            # nothing but the header: the hub sent no stats we could show
        lines.append("(No stats were recorded for this round.)")
    return "\n".join(lines)
