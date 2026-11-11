"""Logins: gyms, coaches and athletes.

Everything a signed-in user sees is limited to the athletes of their gym.
An athlete's NFC card ID (card_uid) is what links the rounds the Pi uploads
to their account. Their player ID, used in page addresses, is their username
in upper case.
"""
import os
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

import db

_USER_SQL = """SELECT u.id, u.username, u.role, u.name, u.gym_id, u.card_uid, u.password,
                      g.name AS gym, g.code AS gym_code
               FROM users u JOIN gyms g ON g.id = u.gym_id"""


def secret_key():
    """Signs the login cookie. From SECRET_KEY, or made once and kept in the database."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    with db.begin() as con:
        if not db.q(con, "SELECT value FROM meta WHERE key = 'secret'"):
            db.q(con, "INSERT INTO meta (key, value) VALUES ('secret', :v)", v=secrets.token_hex(32))
        return db.q(con, "SELECT value FROM meta WHERE key = 'secret'")[0]["value"]


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def clean_card(card_uid):
    return "".join(str(card_uid or "").split()).upper()[:32]


def _public(row):
    d = dict(row)
    d.pop("password")
    d["player_id"] = d["username"].upper() if d["role"] == "athlete" else None
    return d


def _check_card(con, card, user_id=None):
    if card == "GUEST" or (card and not card.isalnum()):
        raise ValueError("A card ID is letters and numbers only, for example 04A2B91C.")
    if card and db.q(con, "SELECT 1 FROM users WHERE card_uid = :c AND id != :u", c=card, u=user_id or 0):
        raise ValueError("That card already belongs to another account.")


# ---------- sign up / log in ----------
def create_user(role, username, password, name, gym_name="", gym_code="", card_uid=""):
    """Create an account and return it. Raises ValueError with a message for the form.

    A coach creates a new gym (gym_name). An athlete joins one with the coach's
    gym_code, and can give their card ID now or link it later."""
    username, name = _clean(username, 24).lower(), _clean(name, 16)
    if role not in ("coach", "athlete"):
        raise ValueError("Choose Coach or Athlete.")
    if len(username) < 3 or not username.isalnum() or username == "guest":
        raise ValueError("Username needs at least 3 letters or numbers, with no spaces or symbols.")
    if len(password or "") < 6:
        raise ValueError("Password needs at least 6 characters.")
    if not name:
        raise ValueError("Enter the name to show on the dashboard.")
    with db.begin() as con:
        if db.q(con, "SELECT 1 FROM users WHERE username = :u", u=username):
            raise ValueError("That username is taken.")
        card = None
        if role == "coach":
            gym_name = _clean(gym_name, 32)
            if not gym_name:
                raise ValueError("Enter your gym's name.")
            if db.q(con, "SELECT 1 FROM gyms WHERE LOWER(name) = :n", n=gym_name.lower()):
                raise ValueError("A gym with that name already exists.")
            gym_id = db.q(con, "INSERT INTO gyms (name, code) VALUES (:n, :c) RETURNING id",
                          n=gym_name, c=gym_code or secrets.token_hex(3).upper())[0]["id"]
        else:
            gym = db.q(con, "SELECT id FROM gyms WHERE code = :c", c=_clean(gym_code, 12).upper())
            if not gym:
                raise ValueError("No gym has that code. Ask your coach for the gym code.")
            gym_id, card = gym[0]["id"], clean_card(card_uid) or None
            _check_card(con, card)
        uid = db.q(con,
                   """INSERT INTO users (username, password, role, name, gym_id, card_uid, created_at)
                      VALUES (:u, :p, :r, :n, :g, :c, :t) RETURNING id""",
                   u=username, p=generate_password_hash(password), r=role, n=name, g=gym_id, c=card,
                   t=time.time())[0]["id"]
    return get_user(uid)


def check_login(role, username, password):
    """The account, or None. The role must match: coaches use the coach login."""
    found = db.rows(_USER_SQL + " WHERE u.username = :u AND u.role = :r",
                    u=_clean(username, 24).lower(), r=str(role))
    if found and check_password_hash(found[0]["password"], password or ""):
        return _public(found[0])
    return None


def get_user(user_id):
    found = db.rows(_USER_SQL + " WHERE u.id = :i", i=user_id)
    return _public(found[0]) if found else None


def set_card(user_id, card_uid):
    """Link (or change) an athlete's card. Raises ValueError with a message for the form."""
    card = clean_card(card_uid)
    if not card:
        raise ValueError("Enter the card ID.")
    with db.begin() as con:
        _check_card(con, card, user_id)
        db.q(con, "UPDATE users SET card_uid = :c WHERE id = :i AND role = 'athlete'", c=card, i=user_id)


def delete_user(user_id):
    with db.begin() as con:
        db.q(con, "DELETE FROM users WHERE id = :i", i=user_id)


# ---------- who can see whom ----------
def athletes(gym_id):
    """Every athlete in one gym. This is the whole visibility rule: pages only ever show these."""
    return [_public(r) for r in db.rows(
        _USER_SQL + " WHERE u.gym_id = :g AND u.role = 'athlete' ORDER BY u.name", g=gym_id)]


def athlete(gym_id, player_id):
    """One athlete of this gym by player ID, or None (also None for athletes of other gyms)."""
    found = db.rows(_USER_SQL + " WHERE u.gym_id = :g AND u.role = 'athlete' AND u.username = :u",
                    g=gym_id, u=str(player_id).lower())
    return _public(found[0]) if found else None


def by_card(card_uid):
    """The athlete who owns a card, or None (used for the name on the live screen)."""
    found = db.rows(_USER_SQL + " WHERE u.card_uid = :c", c=clean_card(card_uid))
    return _public(found[0]) if found else None


def all_cards():
    return [r["card_uid"] for r in db.rows("SELECT card_uid FROM users WHERE card_uid IS NOT NULL")]


# ---------- demo accounts (python app.py --sim) ----------
DEMO_PASSWORD = "demo1234"
DEMO = [  # gym, code, coach username, [(athlete username, name, card)]
    ("Southpaw Gym", "SOUTH1", "coach", [("kai", "Kai", "04A2B91C"), ("mei", "Mei", "04C7D2E5")]),
    ("Northside Boxing", "NORTH1", "coach2", [("arjun", "Arjun", "04F10A33"), ("sam", "Sam", "04B8E9F0")]),
]


def seed_demo():
    """Two gyms, each with a coach and two athletes. Only runs on an empty database."""
    if db.rows("SELECT 1 FROM users LIMIT 1"):
        return
    for gym, code, coach, people in DEMO:
        create_user("coach", coach, DEMO_PASSWORD, "Coach", gym_name=gym, gym_code=code)
        for username, name, card in people:
            create_user("athlete", username, DEMO_PASSWORD, name, gym_code=code, card_uid=card)
