"""
pi_admin.py - Southpaw OS Telegram bot

Two commands for the Pi's OWNER only (chat ids listed in ADMIN_CHAT_IDS in .env):

    /status     the Pi's IP address (for SSH from a phone app), which SouthpawOS programs
                are running, and how long the Pi has been on
    /shutdown   switch the Pi off properly, so it's safe to unplug (asks "/shutdown yes" first)

Switching the Pi off needs root. The bot isn't root, so a one-line sudo rule lets this ONE
account run exactly this ONE command without a password, and nothing else:
    <account> ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
allow_shutdown.sh (in this folder) adds that rule once.

No Telegram code in here, so test_core.py can test it (the commands are passed in).
"""

import subprocess

SERVICES = ("southpaw-hub", "southpaw-bot", "southpaw-nfc", "southpaw-vision")
POWEROFF = ["sudo", "-n", "/usr/bin/systemctl", "poweroff"]   # -n: fail instead of asking for a password


def admin_chat_ids(text):
    """'123456789, -100222' (from .env) -> {123456789, -100222}. Anything that isn't a number is skipped."""
    ids = set()
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def run(command, timeout=5):
    """Run a command and return what it printed, or None if it can't run (e.g. on Windows)."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def ip_addresses(run=run):
    """The Pi's IP addresses, e.g. ['172.20.10.3']. `hostname -I` lists every network it's on."""
    out = run(["hostname", "-I"])
    return [a for a in (out or "").split() if ":" not in a]    # IPv4 only: those are the ones to type


def service_states(run=run):
    """{'southpaw-hub': 'active', ...}; 'unknown' if systemctl isn't there."""
    out = run(["systemctl", "is-active", *SERVICES])
    states = (out or "").split()
    return {s: (states[i] if i < len(states) else "unknown") for i, s in enumerate(SERVICES)}


def uptime_s(path="/proc/uptime"):
    """Seconds since the Pi was switched on, or None if unknown."""
    try:
        with open(path) as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def status_text(ips, states, uptime):
    """The /status reply (Telegram HTML)."""
    lines = ["🖥️ <b>Pi status</b>"]
    if ips:
        lines.append("IP address: " + ", ".join(f"<code>{ip}</code>" for ip in ips))
        lines.append(f"SSH from a phone app: user <code>vision</code>, host <code>{ips[0]}</code>")
    else:
        lines.append("IP address: not found")
    for name, state in states.items():
        icon = "✅" if state == "active" else "⚠️"
        lines.append(f"{icon} {name.replace('southpaw-', '')}: {state}")
    if uptime is not None:
        minutes = int(uptime // 60)
        lines.append(f"On for {minutes // 60} h {minutes % 60} min")
    return "\n".join(lines)


def power_off(run_command=subprocess.run):
    """Ask the Pi to shut down. Returns (True, "") or (False, why it failed)."""
    try:
        result = run_command(POWEROFF, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    return True, ""
